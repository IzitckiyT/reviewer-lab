import errno
import io
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

import run as cli
from lab.transport import TransportError
from lab.ui import serve


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.stdout = redirect_stdout(self.output)
        self.stderr = redirect_stderr(self.output)
        self.stdout.__enter__()
        self.stderr.__enter__()
        self.env = patch.dict('os.environ', {}, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.stderr.__exit__(None, None, None)
        self.stdout.__exit__(None, None, None)

    def test_no_arguments_open_demo_without_codex_or_model_preflight(self):
        with patch('lab.ui.serve') as start_server, patch('run.CodexTransport') as codex:
            self.assertEqual(cli.main([]), 0)
        start_server.assert_called_once_with(cli.ROOT, 8765, True, read_only=True,
                                             fallback_port=True, start_path='#demo')
        codex.assert_not_called()

    def test_start_demo_ignores_live_environment_and_respects_explicit_port(self):
        with patch.dict('os.environ', {'REVIEWER_LAB_DEMO': '0'}), \
                patch('lab.ui.serve') as start_server, patch('run.CodexTransport') as codex:
            self.assertEqual(cli.main(['start', '--port', '0', '--no-open']), 0)
        start_server.assert_called_once_with(cli.ROOT, 0, False, read_only=True,
                                             fallback_port=False, start_path='#demo')
        codex.assert_not_called()

    def test_configured_port_does_not_silently_fall_back(self):
        with patch.dict('os.environ', {'REVIEWER_LAB_PORT': '9000'}), patch('lab.ui.serve') as start_server:
            self.assertEqual(cli.main(['start']), 0)
        self.assertEqual(start_server.call_args.args[1], 9000)
        self.assertFalse(start_server.call_args.kwargs['fallback_port'])

    def test_live_preflight_failure_never_opens_server(self):
        with patch('run.CodexTransport') as codex, patch('lab.ui.serve') as start_server:
            codex.return_value.doctor.side_effect = TransportError('ChatGPT sign-in required')
            self.assertEqual(cli.main(['start', '--live']), 2)
        start_server.assert_not_called()
        codex.return_value.call.assert_not_called()
        self.assertIn('Live-режим пока не готов', self.output.getvalue())
        self.assertIn('Для демо без аккаунта', self.output.getvalue())

    def test_explicit_live_checks_readiness_before_serving(self):
        events = []
        with patch('run.CodexTransport') as codex, patch('lab.ui.serve') as start_server:
            codex.return_value.doctor.side_effect = lambda: events.append('doctor') or {'cli_path': '/test/codex'}
            start_server.side_effect = lambda *args, **kwargs: events.append('server')
            self.assertEqual(cli.main(['start', '--live']), 0)
        self.assertEqual(events, ['doctor', 'server'])
        codex.return_value.call.assert_not_called()
        self.assertFalse(start_server.call_args.kwargs['read_only'])
        self.assertEqual(start_server.call_args.kwargs['start_path'], '#manual')
        self.assertIn('Доступ к модели проверится при первом запросе', self.output.getvalue())

    def test_doctor_timeout_has_actionable_message_without_traceback(self):
        with patch('run.CodexTransport') as codex:
            codex.return_value.doctor.side_effect = subprocess.TimeoutExpired(['codex', 'login', 'status'], 20)
            self.assertEqual(cli.main(['doctor']), 2)
        self.assertIn('не ответила за 20 секунд', self.output.getvalue())
        self.assertIn('Демо доступно без Codex', self.output.getvalue())

    @staticmethod
    def server(port=49123):
        server = Mock()
        server.server_port = port
        server.allowed_origins = {f'http://127.0.0.1:{port}', f'http://localhost:{port}'}
        server.dashboard.read_only = True
        server.serve_forever.side_effect = KeyboardInterrupt
        return server

    def test_default_occupied_port_is_bound_atomically_and_actual_url_opened(self):
        server = self.server()
        with patch('lab.ui.make_server', side_effect=[OSError(errno.EADDRINUSE, 'busy'), server]) as make, \
                patch('webbrowser.open', return_value=True) as browser:
            serve(cli.ROOT, 8765, True, read_only=True, fallback_port=True, start_path='#demo')
        self.assertEqual([call.args[1] for call in make.call_args_list], [8765, 0])
        browser.assert_called_once_with('http://127.0.0.1:49123#demo')
        self.assertIn('Порт 8765 занят', self.output.getvalue())
        server.dashboard.stop.assert_called_once_with()
        server.server_close.assert_called_once_with()
        self.assertIn('Reviewer Lab остановлен', self.output.getvalue())

    def test_explicit_occupied_port_has_clear_error_and_no_fallback(self):
        with patch('lab.ui.make_server', side_effect=OSError(errno.EADDRINUSE, 'busy')) as make, \
                patch('webbrowser.open') as browser:
            with self.assertRaisesRegex(RuntimeError, 'Порт 8766 занят.*--port 8767'):
                serve(cli.ROOT, 8766, True, read_only=True)
        make.assert_called_once()
        browser.assert_not_called()

    def test_bind_permission_error_is_not_masked_as_occupied_port(self):
        with patch('lab.ui.make_server', side_effect=PermissionError(errno.EACCES, 'denied')) as make:
            with self.assertRaises(PermissionError):
                serve(cli.ROOT, 8765, fallback_port=True)
        make.assert_called_once()

    def test_browser_failure_keeps_server_available_and_prints_manual_action(self):
        server = self.server()
        with patch('lab.ui.make_server', return_value=server), patch('webbrowser.open', return_value=False):
            serve(cli.ROOT, open_browser=True, read_only=True)
        server.serve_forever.assert_called_once_with()
        server.server_close.assert_called_once_with()
        self.assertIn('Скопируйте адрес выше', self.output.getvalue())


if __name__ == '__main__':
    unittest.main()
