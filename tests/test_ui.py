import http.client
import json
import io
import os
import signal
import tempfile
import threading
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, patch
from contextlib import redirect_stderr

from lab.cases import CASES
from lab.oracle import evaluate
from lab.storage import append_json, write_json
from lab.transport import MODELS
from lab.ui import Dashboard, browser_origins, inside, journal, make_server, read_json, validate_binding
import run as cli


def fixture(root):
    path = Path(root) / 'runs' / 'test-run'
    row = dict(id='owner-independent-r1-a1', case='owner', stage='search',
               mode='independent', repeat=1, attempt=1, feedback_items=0)
    folder = path / 'attempts' / row['id']
    folder.mkdir(parents=True)
    write_json(path / 'manifest.json', dict(status='running', cases=['owner'], models=MODELS,
        modes=['independent'], repeats=1, attempts_per_run=1, evidence='offline_test_fixture',
        created_at='2026-10-19T00:00:00+00:00'))
    write_json(path / 'cases.json', [asdict(CASES['owner'])])
    write_json(folder / 'started.json', row)
    write_json(folder / 'oracle.json', evaluate(CASES['owner'], CASES['owner'].seed))
    (folder / 'candidate.py').write_text(CASES['owner'].seed)
    return path, row


class DashboardTests(unittest.TestCase):
    def test_demo_rejects_launches_before_creating_files_or_processes(self):
        with tempfile.TemporaryDirectory() as tmp, patch('lab.ui.subprocess.Popen') as popen:
            app = Dashboard(tmp, read_only=True)
            with self.assertRaises(PermissionError):
                app.launch(dict(preset='feedback', case='owner', effort='medium', attempts=1, repeats=1))
            with self.assertRaises(PermissionError):
                app.launch_manual(dict(case='owner', source=CASES['owner'].seed, run_id=None))
            self.assertFalse((Path(tmp) / 'runs').exists())
            popen.assert_not_called()

    def test_pending_execution_state_distinguishes_owned_exit_from_unknown_controller(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = fixture(tmp)
            app = Dashboard(tmp)
            original_manifest = (path / 'manifest.json').read_bytes()
            # A restarted server cannot prove whether an independent CLI still runs.
            self.assertEqual(app.detail('test-run')['execution_state'], 'unobserved')
            self.assertEqual(app.detail('test-run')['rows'][0]['execution_state'], 'unobserved')
            self.assertEqual(app.list_runs()[0]['execution_state'], 'unobserved')
            app.active_id = 'test-run'
            app.process = Mock()
            app.process.poll.return_value = None
            self.assertEqual(app.detail('test-run')['execution_state'], 'owned_running')
            app.process.poll.return_value = 1
            self.assertEqual(app.detail('test-run')['execution_state'], 'interrupted')
            self.assertEqual(app.detail('test-run')['rows'][0]['execution_state'], 'interrupted')
            self.assertEqual((path / 'manifest.json').read_bytes(), original_manifest)
            manifest = json.loads(original_manifest)
            manifest['status'] = 'completed'
            write_json(path / 'manifest.json', manifest)
            self.assertEqual(app.detail('test-run')['execution_state'], 'finished')

    def test_live_files_can_be_read_mid_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = fixture(tmp)
            (path / 'calls.jsonl').write_text('{"id":"one","status":"started"}\n{"id":')
            self.assertEqual(journal(path / 'calls.jsonl'), [{'id':'one','status':'started'}])
            broken = path / 'partial.json'
            broken.write_text('{"status":')
            self.assertIsNone(read_json(broken))
            data = Dashboard(tmp).detail('test-run')
            self.assertEqual(data['rows'][0]['outcome'], 'running')
            self.assertTrue(data['rows'][0]['oracle']['target_regression'])
            self.assertEqual(data['summary']['modes']['independent']['attempts_recorded'], 0)

    def test_latest_call_record_wins_without_double_counting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = fixture(tmp)
            for status in ('started', 'completed'):
                record = dict(id='001-reviewer', role='reviewer', status=status)
                if status == 'completed':
                    record['usage'] = dict(input_tokens=12, output_tokens=3)
                append_json(path / 'calls.jsonl', record)
            d = Dashboard(tmp).detail('test-run')
            self.assertEqual(len(d['calls']), 1)
            self.assertEqual(d['calls'][0]['status'], 'completed')
            self.assertEqual(d['summary']['cli_invocations'], 1)
            self.assertEqual(d['summary']['usage']['input_tokens'], 12)

    def test_paths_and_symlinks_cannot_leave_runs(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            fixture(tmp)
            app = Dashboard(tmp)
            with self.assertRaises(ValueError):
                app.run_path('../secrets')
            (app.runs / 'outside').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                app.run_path('outside')
            with self.assertRaises(ValueError):
                inside(app.runs, '..', 'secrets')

    def test_launch_and_stop_use_bounded_cli_without_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=Dashboard(tmp)
            fake=Mock()
            fake.poll.return_value=None
            with patch('lab.ui.subprocess.Popen', return_value=fake) as popen:
                result=app.launch(dict(preset='compare',case='owner',effort='low',attempts=4,repeats=1))
                args=popen.call_args.args[0]
                self.assertEqual(args[args.index('--reviewer-effort')+1], 'low')
                self.assertEqual(args[args.index('--modes')+1:], ['independent','feedback'])
                self.assertNotIn('shell',popen.call_args.kwargs)
                self.assertTrue(app.active()['running'])
                self.assertEqual(app.active()['id'],result['id'])
                with self.assertRaises(RuntimeError):
                    app.launch(dict(preset='feedback',case='owner',effort='medium',attempts=2,repeats=1))
                self.assertEqual(popen.call_count,1)
                self.assertTrue(app.stop()['stop_requested'])
                fake.send_signal.assert_called_once_with(signal.SIGINT)
                fake.poll.return_value=2
                self.assertFalse(app.active()['running'])
                self.assertEqual(app.active()['exit_code'],2)

    def test_invalid_launch_settings_do_not_start_processes(self):
        good=dict(preset='feedback',case='owner',effort='medium',attempts=2,repeats=1)
        invalid=[None,{},dict(good,case='owner; command'),dict(good,effort='other'),
                 dict(good,attempts=True),dict(good,repeats=99),dict(good,preset='compare'),
                 dict(good,preset='screen'),dict(good,out='/tmp/anything')]
        with tempfile.TemporaryDirectory() as tmp, patch('lab.ui.subprocess.Popen') as popen:
            for config in invalid:
                with self.subTest(config=config), self.assertRaises(ValueError):
                    Dashboard(tmp).launch(config)
            popen.assert_not_called()


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path,self.row=fixture(self.temp.name)
        self.server=make_server(self.temp.name,0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, data=None, headers=None):
        client=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        client.request('GET' if data is None else 'POST',path,
                       None if data is None else json.dumps(data),headers or {})
        response=client.getresponse()
        body=response.read()
        result=(response.status,dict(response.getheaders()),body)
        client.close()
        return result

    def restart(self, **settings):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.server = make_server(self.temp.name, 0, **settings)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def test_demo_discloses_mode_and_rejects_all_controller_actions(self):
        self.restart(read_only=True)
        code, _, body = self.request('/api/state')
        self.assertEqual(code, 200)
        state = json.loads(body)
        self.assertTrue(state['read_only'])
        with patch.object(self.server.dashboard, 'launch') as launch, \
                patch.object(self.server.dashboard, 'launch_manual') as manual, \
                patch.object(self.server.dashboard, 'stop') as stop:
            for path in ('/api/start', '/api/manual', '/api/stop'):
                code, _, body = self.request(path, {}, {'X-Lab-Token': state['token']})
                self.assertEqual(code, 403, path)
                self.assertEqual(json.loads(body)['code'], 'demo_read_only')
            launch.assert_not_called()
            manual.assert_not_called()
            stop.assert_not_called()

    def test_demo_keeps_local_checks_and_replays_without_model_or_disk_writes(self):
        self.restart(read_only=True)
        before = {str(p): p.read_bytes() for p in Path(self.temp.name).rglob('*') if p.is_file()}
        headers = {'X-Lab-Token': self.server.dashboard.token}
        with patch('lab.ui.subprocess.Popen') as popen:
            code, _, body = self.request('/api/manual/check',
                dict(case='owner', source=CASES['owner'].seed), headers)
            self.assertEqual(code, 200)
            self.assertTrue(json.loads(body)['oracle']['target_regression'])
            code, _, body = self.request('/api/replay',
                dict(run='test-run', attempt=self.row['id']), headers)
            self.assertEqual(code, 200)
            self.assertTrue(json.loads(body)['target_regression'])
            popen.assert_not_called()
        after = {str(p): p.read_bytes() for p in Path(self.temp.name).rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_external_port_mapping_uses_exact_host_origin_and_token(self):
        self.restart(bind='0.0.0.0', read_only=True,
                     public_origins=['http://localhost:9876', 'http://127.0.0.1:9876'])
        payload = dict(case='owner', source=CASES['owner'].seed)
        headers = {'Host': 'localhost:9876', 'Origin': 'http://localhost:9876',
                   'X-Lab-Token': self.server.dashboard.token}
        self.assertEqual(self.request('/api/state', headers={'Host': 'localhost:9876'})[0], 200)
        self.assertEqual(self.request('/api/manual/check', payload, headers)[0], 200)
        # Internal port, cross-site suffix, wildcard/null origins and missing tokens stay forbidden.
        bad_headers = [dict(headers, Host='localhost:8765'),
                       dict(headers, Host='localhost:9876.attacker.example'),
                       dict(headers, Origin='http://localhost:9876.attacker.example'),
                       dict(headers, Origin='http://localhost:8765'),
                       dict(headers, Origin='null'), dict(headers, Origin='*'),
                       {k: v for k, v in headers.items() if k != 'X-Lab-Token'}]
        for bad in bad_headers:
            with self.subTest(headers=bad):
                self.assertEqual(self.request('/api/manual/check', payload, bad)[0], 403)

    def test_duplicate_host_headers_are_rejected(self):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        client.putrequest('GET', '/api/state', skip_host=True)
        host = f'127.0.0.1:{self.server.server_port}'
        client.putheader('Host', host)
        client.putheader('Host', host)
        client.endheaders()
        response = client.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        client.close()

    def test_view_and_replay_do_not_call_models(self):
        with patch('lab.ui.subprocess.Popen') as popen:
            code,headers,body=self.request('/')
            self.assertEqual(code,200)
            self.assertIn('frame-ancestors',headers['Content-Security-Policy'])
            self.assertIn('Reviewer Lab',body.decode())
            code,_,body=self.request('/api/state')
            state=json.loads(body)
            self.assertEqual(len(state['runs']),1)
            code,_,body=self.request('/api/runs/test-run')
            self.assertEqual(json.loads(body)['rows'][0]['source'],CASES['owner'].seed)
            code,_,body=self.request('/api/replay',dict(run='test-run',attempt=self.row['id']),
                                     {'X-Lab-Token':state['token']})
            self.assertEqual(code,200)
            self.assertTrue(json.loads(body)['target_regression'])
            self.assertEqual(len(json.loads(body)['rows']),14)
            popen.assert_not_called()

    def test_bundled_fonts_are_served_locally(self):
        code, headers, body = self.request('/fonts.css')
        self.assertEqual(code, 200)
        self.assertIn(b'IBM Plex Sans', body)
        self.assertNotIn(b'https://', body)
        for family in ('sans', 'mono'):
            for weight in (400, 500):
                code, headers, body = self.request(f'/fonts/plex-{family}-{weight}.ttf')
                self.assertEqual(code, 200)
                self.assertEqual(headers['Content-Type'], 'font/ttf')
                self.assertEqual(body[:4], b'\x00\x01\x00\x00')
                self.assertGreater(len(body), 100_000)
        for face in ('regular', 'italic'):
            code, headers, body = self.request(f'/fonts/lora-{face}.ttf')
            self.assertEqual(code, 200)
            self.assertEqual(headers['Content-Type'], 'font/ttf')
            self.assertEqual(body[:4], b'\x00\x01\x00\x00')
        self.assertEqual(self.request('/workspace.css')[0], 200)
        self.assertEqual(self.request('/fonts/../../README.md')[0], 404)

    def test_case_explanations_are_loaded_and_served_without_external_scripts(self):
        code, _, body = self.request('/')
        self.assertEqual(code, 200)
        self.assertLess(body.index(b'src="/case-copy.js"'), body.index(b'src="/test-explanation.js"'))
        self.assertLess(body.index(b'src="/test-explanation.js"'), body.index(b'src="/app.js"'))
        self.assertLess(body.index(b'src="/case-copy.js"'), body.index(b'src="/task-library.js"'))
        self.assertLess(body.index(b'src="/task-library.js"'), body.index(b'src="/app.js"'))
        for name, marker in [('case-copy.js', b'LabCaseCopy'), ('test-explanation.js', b'LabTestView'), ('task-library.js', b'LabTaskLibrary')]:
            code, headers, body = self.request('/' + name)
            self.assertEqual(code, 200)
            self.assertEqual(headers['Content-Type'], 'text/javascript; charset=utf-8')
            self.assertIn(marker, body)
        self.assertEqual(self.request('/../case-copy.js')[0], 404)
        for name in ('task-library.css', 'navigation.css'):
            code, headers, _ = self.request('/' + name)
            self.assertEqual(code, 200)
            self.assertEqual(headers['Content-Type'], 'text/css; charset=utf-8')
        self.assertEqual(self.request('/../task-library.js')[0], 404)

    def test_cross_origin_and_missing_token_cannot_start_run(self):
        payload=dict(preset='feedback',case='owner',effort='medium',attempts=2,repeats=1)
        with patch.object(self.server.dashboard,'launch') as launch:
            self.assertEqual(self.request('/api/start',payload)[0],403)
            self.assertEqual(self.request('/api/start',payload,{
                'X-Lab-Token':self.server.dashboard.token,'Origin':'https://other.example'})[0],403)
            self.assertEqual(self.request('/api/state',headers={'Host':'other.example'})[0],403)
            launch.assert_not_called()

    def test_authorized_form_passes_only_explicit_settings(self):
        payload=dict(preset='screen',case='owner',effort='medium',attempts=1,repeats=3)
        with patch.object(self.server.dashboard,'launch',return_value={'id':'stub'}) as launch:
            code,_,_=self.request('/api/start',payload,{'X-Lab-Token':self.server.dashboard.token})
            self.assertEqual(code,201)
            launch.assert_called_once_with(payload)
        self.assertEqual(self.request('/api/runs/..')[0],404)
        self.assertEqual(self.request('/anything-on-disk')[0],404)

    def test_manual_check_accepts_code_and_does_not_start_a_process(self):
        source = '# ' + 'x' * 6000 + '\n' + CASES['owner'].seed
        payload = dict(case='owner', source=source)
        with patch('lab.ui.subprocess.Popen') as popen:
            self.assertEqual(self.request('/api/manual/check', payload)[0], 403)
            status, _, body = self.request('/api/manual/check', payload,
                {'X-Lab-Token': self.server.dashboard.token})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['local_result'], 'target_regression')
            self.assertEqual(len(json.loads(body)['oracle']['rows']), 14)
            self.assertEqual(self.request('/api/manual/check', dict(payload, source='я' * 10001),
                {'X-Lab-Token': self.server.dashboard.token})[0], 400)
            popen.assert_not_called()

    def test_manual_launch_has_same_origin_and_shape_validation(self):
        payload = dict(case='owner', source=CASES['owner'].seed, run_id=None)
        headers = {'X-Lab-Token': self.server.dashboard.token}
        with patch.object(self.server.dashboard, 'launch_manual', return_value={'id': 'manual-stub'}) as launch:
            self.assertEqual(self.request('/api/manual', payload,
                dict(headers, Origin='https://other.example'))[0], 403)
            launch.assert_not_called()
            status, _, body = self.request('/api/manual', payload, headers)
            self.assertEqual(status, 201)
            self.assertEqual(json.loads(body), {'id': 'manual-stub'})
            launch.assert_called_once_with(payload)
        with patch('lab.ui.subprocess.Popen') as popen:
            for bad in (dict(payload, run_id='test-run'), dict(payload, run_id='../bad'),
                        dict(payload, run_id='missing'), dict(payload, source=''), {}):
                self.assertEqual(self.request('/api/manual', bad, headers)[0], 400)
            popen.assert_not_called()


class ServerConfigurationTests(unittest.TestCase):
    def test_network_facing_bind_requires_demo(self):
        validate_binding('127.0.0.1', False)
        validate_binding('0.0.0.0', True)
        for bind in ('0.0.0.0', '192.168.1.5'):
            with self.assertRaisesRegex(ValueError, 'requires --demo'):
                validate_binding(bind, False)
        for bind in ('localhost', '*', '127.0.0.1:8765', '::1'):
            with self.assertRaises(ValueError):
                validate_binding(bind, True)

    def test_origin_configuration_rejects_ambiguous_values(self):
        self.assertEqual(browser_origins(['https://demo.example:443'], 8765), {'https://demo.example:443'})
        for origin in ('https://demo.example/', 'https://user@demo.example', 'file://demo.example',
                       '*', '', 'http://*.example', 'http://localhost:0', 'http://localhost:99999',
                       'http://localhost:8765?x=1', 'http://localhost:8765#x', ' http://localhost:8765'):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                browser_origins([origin], 8765)

    def test_cli_keeps_native_defaults_and_reads_explicit_demo_configuration(self):
        with patch.dict(os.environ, {}, clear=True), patch('lab.ui.serve') as serve:
            self.assertEqual(cli.main(['ui']), 0)
            serve.assert_called_once_with(cli.ROOT, 8765, False, bind='127.0.0.1',
                                          read_only=False, public_origins=None)
        env = dict(REVIEWER_LAB_BIND='0.0.0.0', REVIEWER_LAB_PORT='9876', REVIEWER_LAB_DEMO='1',
                   REVIEWER_LAB_PUBLIC_ORIGINS='http://localhost:9876,http://127.0.0.1:9876')
        with patch.dict(os.environ, env, clear=True), patch('lab.ui.serve') as serve:
            self.assertEqual(cli.main(['ui']), 0)
            serve.assert_called_once_with(cli.ROOT, 9876, False, bind='0.0.0.0', read_only=True,
                public_origins=['http://localhost:9876', 'http://127.0.0.1:9876'])
        with patch.dict(os.environ, env, clear=True), patch('lab.ui.serve') as serve:
            self.assertEqual(cli.main(['ui', '--port', '8766', '--bind', '127.0.0.1', '--demo',
                                       '--public-origin', 'http://localhost:8766']), 0)
            serve.assert_called_once_with(cli.ROOT, 8766, False, bind='127.0.0.1',
                                          read_only=True, public_origins=['http://localhost:8766'])

    def test_bad_environment_fails_before_serving(self):
        for env in ({'REVIEWER_LAB_DEMO': 'maybe'}, {'REVIEWER_LAB_PORT': 'not-a-port'}):
            with patch.dict(os.environ, env, clear=True), patch('lab.ui.serve') as serve, \
                    redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(['ui']), 2)
                serve.assert_not_called()


if __name__=='__main__':
    unittest.main()
