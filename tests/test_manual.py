import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from lab.cases import CASES
from lab.manual import check_source, load_series, run_manual_attempt
from lab.storage import append_json, read_jsonl, recover_jsonl_tail, write_json
from lab.transport import CodexTransport, TransportError
from lab.ui import Dashboard
from lab.verify import verify_run


class ManualFixtureTransport:
    evidence = "offline_test_fixture"
    options = []
    prompts = []
    fail_on = None

    def __init__(self, root, **kwargs):
        self.root = Path(root)
        type(self).options.append(kwargs)
        self.calls = len({r['id'] for r in read_jsonl(self.root / 'calls.jsonl')})
        self.tokens = self.calls * 15

    def doctor(self):
        return {'fixture': True}

    def call(self, role, prompt, instructions, schema):
        self.calls += 1
        identity = f'{self.calls:03d}-{role}'
        self.last_call_id = identity
        folder = self.root / 'calls' / identity
        folder.mkdir(parents=True)
        (folder / 'prompt.txt').write_text(prompt)
        type(self).prompts.append((role, json.loads(prompt)))
        append_json(self.root / 'calls.jsonl', {'id': identity, 'role': role,
                    'usage': {'input_tokens': 10, 'output_tokens': 5}})
        if self.calls == self.fail_on:
            raise TransportError('Simulated unavailable reviewer')
        self.tokens += 15
        return {'answer': {'verdict': 'PASS', 'reason': 'Offline fixture'}, 'call_id': identity}


class ManualTests(unittest.TestCase):
    def setUp(self):
        ManualFixtureTransport.options = []
        ManualFixtureTransport.prompts = []
        ManualFixtureTransport.fail_on = None
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'manual'
        self.case = CASES['owner']

    def tearDown(self):
        self.temp.cleanup()

    def submit(self, source):
        return run_manual_attempt(self.root, self.case, source, transport_factory=ManualFixtureTransport)

    def test_offline_gate_distinguishes_results_and_never_initializes_transport(self):
        examples = [('def ???', 'local_invalid'), (self.case.original, 'rule_preserved'),
                    ('def decision(role, approved):\n    return True\n', 'different_regression')]
        for source, outcome in examples:
            self.assertEqual(self.submit(source)['status'], 'completed')
            row = read_jsonl(self.root / 'attempts.jsonl')[-1]
            self.assertEqual(row['outcome'], outcome)
            self.assertEqual(row['reviewer']['verdict'], 'NOT_CALLED')
        self.assertEqual(ManualFixtureTransport.options, [])
        self.assertFalse((self.root / 'calls').exists())
        self.assertTrue(verify_run(self.root)['ok'])

    def test_series_appends_immutable_attempts_and_controls_only_once(self):
        self.submit(self.case.seed)
        first = self.root / 'attempts/owner-manual-r1-a1'
        saved = {p.name: p.read_bytes() for p in first.iterdir()}
        self.submit(self.case.seed.replace('return', '# changed manually\n    return'))
        rows = read_jsonl(self.root / 'attempts.jsonl')
        self.assertEqual(len(rows), 4)
        search = [r for r in rows if r['stage'] == 'search']
        self.assertEqual([r['attempt'] for r in search], [1, 2])
        self.assertEqual([r['reviewer_call'] for r in search], ['003-reviewer', '004-reviewer'])
        self.assertEqual({p.name: p.read_bytes() for p in first.iterdir()}, saved)
        self.assertTrue(all(set(o['models']) == {'reviewer'} and o['resume'] for o in ManualFixtureTransport.options))
        self.assertEqual([o['max_calls'] for o in ManualFixtureTransport.options], [3, 4])
        self.assertTrue(all(role == 'reviewer' for role, _ in ManualFixtureTransport.prompts))
        for _, prompt in ManualFixtureTransport.prompts:
            self.assertNotIn('feedback_history', prompt)
            self.assertNotIn('target', prompt['case'])
        self.assertTrue(verify_run(self.root)['ok'])

    def test_interrupted_controls_resume_without_repeating_completed_safe(self):
        ManualFixtureTransport.fail_on = 2
        self.assertEqual(self.submit(self.case.seed)['status'], 'interrupted')
        ManualFixtureTransport.fail_on = None
        self.assertEqual(self.submit(self.case.seed)['status'], 'completed')
        rows = read_jsonl(self.root / 'attempts.jsonl')
        self.assertEqual(sum(r.get('control') == 'safe' for r in rows), 1)
        self.assertEqual(sum(r.get('control') == 'seed' for r in rows), 2)
        self.assertEqual(rows[-1]['reviewer_call'], '004-reviewer')
        self.assertEqual(rows[2]['reviewer']['verdict'], 'NOT_CALLED')
        self.assertTrue(verify_run(self.root)['ok'])

    def test_reuse_rejects_changed_case_or_nonmanual_directory(self):
        self.submit(self.case.original)
        with self.assertRaises(ValueError):
            run_manual_attempt(self.root, CASES['audit'], CASES['audit'].seed,
                               transport_factory=ManualFixtureTransport)
        path = self.root / 'manifest.json'
        data = json.loads(path.read_text())
        data['experiment_kind'] = 'adaptive_search'
        write_json(path, data)
        with self.assertRaises(ValueError):
            load_series(self.root, 'owner')
        self.assertEqual(len(read_jsonl(self.root / 'attempts.jsonl')), 1)

    def test_offline_audit_checks_manually_saved_reviewer_prompt(self):
        self.submit(self.case.seed)
        prompt = self.root / 'attempts/owner-manual-r1-a1/reviewer-prompt.txt'
        prompt.write_text('different context')
        result = verify_run(self.root)
        self.assertFalse(result['ok'])
        self.assertTrue(any('Manual reviewer prompt differs' in error for error in result['errors']))

    def test_abandoned_attempt_is_finalized_without_overwriting_its_source(self):
        self.submit(self.case.original)
        old = read_jsonl(self.root / 'attempts.jsonl')[0]
        folder = self.root / 'attempts' / old['id']
        # Simulate termination after preparing the attempt but before journaling its result.
        (self.root / 'attempts.jsonl').write_text('')
        (folder / 'result.json').unlink()
        previous_source = (folder / 'candidate.py').read_bytes()
        self.submit(self.case.safe)
        rows = read_jsonl(self.root / 'attempts.jsonl')
        self.assertEqual([row['attempt'] for row in rows], [1, 2])
        self.assertEqual(rows[0]['outcome'], 'interrupted')
        self.assertEqual((folder / 'candidate.py').read_bytes(), previous_source)
        self.assertEqual(ManualFixtureTransport.options, [])
        self.assertTrue(verify_run(self.root)['ok'])

    def test_finished_result_survives_a_missing_journal_append(self):
        self.submit(self.case.original)
        result = self.root / 'attempts/owner-manual-r1-a1/result.json'
        previous = result.read_bytes()
        (self.root / 'attempts.jsonl').write_text('')
        self.submit(self.case.safe)
        self.assertEqual(result.read_bytes(), previous)
        rows = read_jsonl(self.root / 'attempts.jsonl')
        self.assertEqual([row['outcome'] for row in rows], ['rule_preserved', 'rule_preserved'])
        self.assertTrue(verify_run(self.root)['ok'])

    def test_torn_journal_tails_are_backed_up_and_finished_results_are_recovered(self):
        self.submit(self.case.original)
        original_row = read_jsonl(self.root / 'attempts.jsonl')[0]
        damaged = {
            'attempts.jsonl': b'{"id":',
            'calls.jsonl': b'{"id":"001-reviewer","reason":"\xd1',
        }
        for name, contents in damaged.items():
            (self.root / name).write_bytes(contents)
        manifest = self.submit(self.case.safe)
        self.assertEqual(manifest['status'], 'completed')
        rows = read_jsonl(self.root / 'attempts.jsonl')
        self.assertEqual(rows[0], original_row)
        self.assertEqual([r['attempt'] for r in rows], [1, 2])
        for recovery in manifest['journal_recoveries']:
            self.assertEqual((self.root / recovery['backup']).read_bytes(),
                             damaged[recovery['journal']])
        self.assertEqual(len(manifest['journal_recoveries']), 2)
        self.assertTrue(verify_run(self.root)['ok'])

    def test_complete_record_missing_only_newline_is_preserved_before_append(self):
        self.submit(self.case.original)
        journal = self.root / 'attempts.jsonl'
        original = journal.read_bytes().rstrip(b'\n')
        journal.write_bytes(original)
        manifest = self.submit(self.case.safe)
        self.assertEqual([r['attempt'] for r in read_jsonl(journal)], [1, 2])
        recovery = manifest['journal_recoveries'][0]
        self.assertEqual(recovery['discarded_tail_bytes'], 0)
        self.assertEqual((self.root / recovery['backup']).read_bytes(), original)
        self.assertTrue(verify_run(self.root)['ok'])

    def test_recovery_rejects_corrupt_middle_and_complete_bad_records(self):
        self.submit(self.case.original)
        journal = self.root / 'attempts.jsonl'
        valid = journal.read_bytes()
        for damaged in (b'{"id":\n' + valid, valid + b'{"id":\n'):
            with self.subTest(damaged=damaged):
                journal.write_bytes(damaged)
                with self.assertRaises(ValueError):
                    recover_jsonl_tail(journal)
                self.assertEqual(journal.read_bytes(), damaged)
        self.assertEqual(list(self.root.glob('*.damaged-*')), [])

    def test_failed_candidate_call_is_distinct_from_not_called(self):
        ManualFixtureTransport.fail_on = 3
        self.assertEqual(self.submit(self.case.seed)['status'], 'interrupted')
        row = read_jsonl(self.root / 'attempts.jsonl')[-1]
        self.assertEqual(row['reviewer']['verdict'], 'ERROR')
        self.assertEqual(row['reviewer_failed_call'], '003-reviewer')
        self.assertNotIn('reviewer_call', row)
        self.assertFalse(row['success'])
        self.assertTrue(verify_run(self.root)['ok'])

    def test_old_invalid_oracle_without_diagnostics_remains_verifiable(self):
        self.submit('def ???')
        row = read_jsonl(self.root / 'attempts.jsonl')[0]
        folder = self.root / 'attempts' / row['id']
        row['oracle'].pop('diagnostic', None)
        write_json(folder / 'oracle.json', row['oracle'])
        write_json(folder / 'result.json', row)
        (self.root / 'attempts.jsonl').write_text(json.dumps(row) + '\n')
        self.assertTrue(verify_run(self.root)['ok'])
        row['oracle']['reason'] = 'Changed scientific validation reason'
        write_json(folder / 'oracle.json', row['oracle'])
        write_json(folder / 'result.json', row)
        (self.root / 'attempts.jsonl').write_text(json.dumps(row) + '\n')
        self.assertFalse(verify_run(self.root)['ok'])

    def test_recovered_calls_keep_known_usage_and_mark_unknown_state(self):
        self.submit(self.case.original)
        for identity in ('001-reviewer', '002-reviewer'):
            folder = self.root / 'calls' / identity
            folder.mkdir(parents=True)
            record = {'id': identity, 'role': 'reviewer', 'status': 'started', 'usage': None}
            write_json(folder / 'call.json', record)
            append_json(self.root / 'calls.jsonl', record)
        event = {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 5}}
        (self.root / 'calls/001-reviewer/events.jsonl').write_text(json.dumps(event) + '\n{"type":')
        manifest = self.submit(self.case.safe)
        self.assertEqual(manifest['known_tokens'], 15)
        self.assertEqual(manifest['cli_invocations_started'], 2)
        calls = {row['id']: row for row in read_jsonl(self.root / 'calls.jsonl')}
        self.assertEqual({row['status'] for row in calls.values()}, {'interrupted'})
        self.assertIsNone(calls['002-reviewer']['usage'])
        self.assertIn('unknown', calls['002-reviewer']['error'])
        report = json.loads((self.root / 'report.json').read_text())
        self.assertEqual(report['unknown_usage_calls'], 1)
        self.assertEqual(report['usage']['input_tokens'], 10)
        transport = CodexTransport(self.root, codex=sys.executable, resume=True)
        self.assertEqual(transport.tokens, 15)
        self.assertEqual(transport.next_call_number, 3)
        self.assertEqual(ManualFixtureTransport.options, [])

    def test_manual_series_has_readable_dashboard_label(self):
        self.root = Path(self.temp.name) / 'runs' / 'manual-fixture'
        self.submit(self.case.original)
        runs = Dashboard(self.temp.name).list_runs()
        self.assertEqual(runs[0]['label'], 'Ручная серия · Публикация только владельцем')

    def test_invalid_inputs_rejected_before_creation(self):
        for case, source in [('owner', ''), ('owner', 'я' * 10001), ('bogus', self.case.seed),
                             ('owner', None), ([], self.case.seed)]:
            with self.subTest(case=case), self.assertRaises(ValueError):
                check_source(case, source)
        self.assertFalse(self.root.exists())

    def test_transport_resumes_ids_and_budget_from_real_saved_calls(self):
        self.root.mkdir()
        answer = json.dumps({'verdict': 'BLOCK', 'reason': 'Fixture'})
        events = '\n'.join(json.dumps(event) for event in [
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': answer}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 5}}])
        script = 'import sys; sys.stdin.read(); print(' + repr(events) + ')'
        for number in (1, 2):
            transport = CodexTransport(self.root, codex=sys.executable, resume=True, max_calls=2)
            with patch.object(transport, 'argv', return_value=[sys.executable, '-c', script]):
                result = transport.call('reviewer', '{}', 'fixture', {})
            self.assertEqual(result['call_id'], f'{number:03d}-reviewer')
            self.assertEqual(transport.tokens, number * 15)
        transport = CodexTransport(self.root, codex=sys.executable, resume=True, max_calls=2)
        with self.assertRaises(TransportError):
            transport.call('reviewer', '{}', 'fixture', {})

    def test_dashboard_validates_series_and_uses_owned_subprocess(self):
        app = Dashboard(self.temp.name)
        fake = Mock()
        fake.poll.return_value = None
        captured = []
        def start(args, **kwargs):
            captured.append(kwargs['stdin'].read())
            return fake
        payload = {'case': 'owner', 'source': self.case.original, 'run_id': None}
        with patch('lab.ui.subprocess.Popen', side_effect=start) as popen:
            result = app.launch_manual(payload)
            self.assertTrue(result['id'].startswith('manual-'))
            self.assertEqual(captured, [self.case.original])
            self.assertIn('manual', popen.call_args.args[0])
            self.assertNotIn('shell', popen.call_args.kwargs)
            with self.assertRaises(RuntimeError):
                app.launch_manual(payload)
            fake.poll.return_value = 0
            for run_id in ('../bad', [], 'missing'):
                with self.subTest(run_id=run_id), self.assertRaises((ValueError, FileNotFoundError)):
                    app.launch_manual(dict(payload, run_id=run_id))
            self.assertEqual(popen.call_count, 1)


if __name__ == '__main__':
    unittest.main()
