"""Provider selection, local activation and evidence verification across layers."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from lab.cases import CASES
from lab.engine import run_campaign
from lab.manual import run_manual_attempt
from lab.storage import read_jsonl
from lab.transport import TransportError
from lab.providers import schema_instructions
from lab.ui import Dashboard, make_server
from lab.verify import verify_run


class ActivationTests(unittest.TestCase):
    def test_activation_checks_selected_provider_without_inference(self):
        with tempfile.TemporaryDirectory() as tmp, patch('lab.ui.create_transport') as factory:
            app = Dashboard(tmp, read_only=True)
            factory.return_value.doctor.return_value = {'provider': 'claude'}
            response = app.enable_live({'provider': 'claude'})
            self.assertFalse(app.read_only)
            self.assertEqual(app.selected_provider, 'claude')
            self.assertEqual(response['provider'], 'claude')
            factory.assert_called_once_with(app.root, provider='claude')
            factory.return_value.call.assert_not_called()
            self.assertFalse((Path(tmp) / 'runs').exists())

    def test_failed_activation_leaves_demo_enabled(self):
        with tempfile.TemporaryDirectory() as tmp, patch('lab.ui.create_transport') as factory:
            app = Dashboard(tmp, read_only=True)
            factory.side_effect = TransportError('DEEPSEEK_API_KEY is missing')
            with self.assertRaisesRegex(RuntimeError, 'missing'):
                app.enable_live({'provider': 'deepseek'})
            self.assertTrue(app.read_only)

    def test_codex_activation_checks_login_without_requiring_default_models(self):
        with tempfile.TemporaryDirectory() as tmp, patch('lab.ui.create_transport') as factory:
            app = Dashboard(tmp, read_only=True)
            factory.return_value.doctor.return_value = {'authentication': 'ChatGPT'}
            app.enable_live({'provider': 'codex'})
            factory.return_value.doctor.assert_called_once_with(check_models=False)
            factory.return_value.call.assert_not_called()

    def test_network_facing_demo_cannot_enable_live(self):
        with tempfile.TemporaryDirectory() as tmp, patch('lab.ui.create_transport') as factory:
            server = make_server(tmp, port=0, bind='0.0.0.0', read_only=True)
            try:
                with self.assertRaises(PermissionError):
                    server.dashboard.enable_live({'provider': 'codex'})
                factory.assert_not_called()
            finally:
                server.server_close()

    def test_activation_http_requires_token_and_same_origin(self):
        with tempfile.TemporaryDirectory() as tmp, patch('lab.ui.create_transport') as factory:
            server = make_server(tmp, port=0, read_only=True)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            factory.return_value.doctor.return_value = {'provider': 'claude'}
            try:
                origin = f'http://127.0.0.1:{server.server_port}'
                headers = {'Content-Type': 'application/json', 'Origin': origin}
                for token, status in ((None, 403), (server.dashboard.token, 200)):
                    if token:
                        headers['X-Lab-Token'] = token
                    client = http.client.HTTPConnection('127.0.0.1', server.server_port)
                    client.request('POST', '/api/enable-live', json.dumps({'provider': 'claude'}), headers)
                    response = client.getresponse()
                    self.assertEqual(response.status, status)
                    response.read()
                    client.close()
                factory.return_value.call.assert_not_called()
            finally:
                server.shutdown()
                server.server_close()
                worker.join()

    def test_provider_and_models_reach_child_without_shell(self):
        with tempfile.TemporaryDirectory() as tmp, patch('lab.ui.subprocess.Popen') as popen:
            popen.return_value.poll.return_value = None
            app = Dashboard(tmp)
            app.launch(dict(preset='feedback', case='owner', effort='medium', attempts=1, repeats=1,
                            provider='claude', reviewer_model='haiku', attacker_model='sonnet'))
            args = popen.call_args.args[0]
            self.assertEqual(args[args.index('--provider') + 1], 'claude')
            self.assertEqual(args[args.index('--reviewer-model') + 1], 'haiku')
            self.assertEqual(args[args.index('--attacker-model') + 1], 'sonnet')
            self.assertNotIn('shell', popen.call_args.kwargs)


class ProviderEvidenceTests(unittest.TestCase):
    @staticmethod
    def completion(verdict):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps({
            'model': 'deepseek-flash',
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
                'verdict': verdict, 'reason': 'Offline provider fixture'})}}],
            'usage': {'prompt_tokens': 11, 'completion_tokens': 4},
        }).encode()
        return response

    def test_api_campaign_verifies_and_tampered_envelope_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'fixture-key'}), \
                patch('lab.providers.urllib.request.build_opener') as builder:
            source = Path(tmp) / 'candidate.py'
            source.write_text(CASES['owner'].seed)
            root = Path(tmp) / 'run'
            builder.return_value.open.side_effect = [self.completion(v) for v in ('PASS', 'BLOCK', 'PASS')]
            manifest = run_campaign(root, [CASES['owner']], attempts=1, modes=('independent',),
                                    candidate_files=[source], provider='deepseek')
            self.assertEqual(manifest['status'], 'completed')
            self.assertEqual(manifest['provider'], 'deepseek')
            self.assertEqual(manifest['known_tokens'], 45)
            self.assertTrue(verify_run(root)['ok'])
            schema_path = root / 'calls/003-reviewer/schema.json'
            request_path = root / 'calls/003-reviewer/request.json'
            original_schema, original_request = schema_path.read_text(), request_path.read_text()
            schema = json.loads(original_schema)
            schema['description'] = 'Unexpected extra context supplied after recording'
            request = json.loads(original_request)
            instructions = (root / 'calls/003-reviewer/instructions.txt').read_text()
            request['body']['messages'][0]['content'] = schema_instructions(instructions, schema)
            schema_path.write_text(json.dumps(schema))
            request_path.write_text(json.dumps(request))
            self.assertTrue(any('Response schema' in e for e in verify_run(root)['errors']))
            schema_path.write_text(original_schema)
            request_path.write_text(original_request)
            envelope = root / 'calls/003-reviewer/provider-response.json'
            saved = json.loads(envelope.read_text())
            saved['content'] = json.dumps({'verdict': 'BLOCK', 'reason': 'Changed after recording'})
            envelope.write_text(json.dumps(saved))
            checked = verify_run(root)
            self.assertFalse(checked['ok'])
            self.assertTrue(any('DeepSeek response differs' in e for e in checked['errors']))

    def test_manual_provider_is_pinned_and_resume_reuses_controls(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'fixture-key'}), \
                patch('lab.providers.urllib.request.build_opener') as builder:
            root = Path(tmp) / 'series'
            builder.return_value.open.side_effect = [self.completion(v) for v in ('PASS', 'BLOCK', 'PASS', 'BLOCK')]
            first = run_manual_attempt(root, CASES['owner'], CASES['owner'].seed, provider='deepseek')
            self.assertEqual(first['status'], 'completed')
            with self.assertRaisesRegex(ValueError, 'Провайдер серии'):
                run_manual_attempt(root, CASES['owner'], CASES['owner'].seed, provider='claude')
            with self.assertRaisesRegex(ValueError, 'Модель серии'):
                run_manual_attempt(root, CASES['owner'], CASES['owner'].seed, reviewer_model='other-model')
            second = run_manual_attempt(root, CASES['owner'], CASES['owner'].seed)
            self.assertEqual(second['cli_invocations_started'], 4)
            self.assertEqual(len(read_jsonl(root / 'attempts.jsonl')), 4)
            self.assertTrue(verify_run(root)['ok'])

    def test_claude_seed_screening_keeps_text_only_evidence_replayable(self):
        with tempfile.TemporaryDirectory() as tmp, patch('lab.providers.shutil.which', return_value='/fixture/claude'), \
                patch('lab.providers.ClaudeTransport.doctor', autospec=True) as doctor, \
                patch('lab.providers.subprocess.Popen') as popen:
            outputs = [json.dumps({'subtype': 'success', 'is_error': False,
                'result': json.dumps({'verdict': verdict, 'reason': 'Offline Claude fixture'}),
                'usage': {'input_tokens': 10, 'output_tokens': 5},
                'modelUsage': {'claude-fixture': {}}}) for verdict in ('PASS', 'BLOCK', 'PASS')]
            popen.return_value.returncode = 0
            popen.return_value.communicate.side_effect = [(value, '') for value in outputs]
            checked_roles = []
            doctor.side_effect = lambda transport: checked_roles.append(set(transport.models)) or {'provider': 'claude'}
            root = Path(tmp) / 'run'
            manifest = run_campaign(root, [CASES['audit']], attempts=1, modes=('independent',),
                                    provider='claude', seed_candidate=True)
            self.assertEqual(manifest['status'], 'completed')
            self.assertEqual(manifest['candidate_files'], ['case-seed:audit'])
            self.assertEqual(manifest['known_tokens'], 45)
            self.assertEqual(checked_roles, [{'reviewer'}])
            self.assertTrue(verify_run(root)['ok'])
            request_path = root / 'calls/003-reviewer/request.json'
            request = json.loads(request_path.read_text())
            request['argv'][request['argv'].index('--effort') + 1] = 'low'
            request_path.write_text(json.dumps(request))
            self.assertFalse(verify_run(root)['ok'])


if __name__ == '__main__':
    unittest.main()
