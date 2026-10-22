"""Connection readiness is explicit, cached and never confused with model access."""
import http.client
import json
import shlex
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from lab.providers import _claude_env, checked_status, setup_status
from lab.transport import CodexTransport, TransportError
from lab.ui import Dashboard, make_server


class ConnectionStatusTests(unittest.TestCase):
    def test_claude_keeps_pinned_runtime_and_prompt_history_disabled(self):
        flags = ('DISABLE_UPDATES', 'DISABLE_AUTOUPDATER',
                 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC', 'CLAUDE_CODE_SKIP_PROMPT_HISTORY')
        with patch.dict('os.environ', {key: '0' for key in flags}, clear=True):
            env = _claude_env()
        for key in flags:
            self.assertEqual(env[key], '1')

    def test_container_setup_uses_commands_in_the_running_service(self):
        with patch.dict('os.environ', {'REVIEWER_LAB_CONTAINER': '1'}, clear=True), \
                patch('lab.providers._cli', return_value='/usr/local/bin/fixture-cli'), \
                patch('lab.providers.subprocess.run') as run, \
                patch('lab.providers.urllib.request.build_opener') as http:
            for provider, command in (
                    ('codex', 'docker compose exec reviewer-lab codex login --device-auth'),
                    ('claude', 'docker compose exec reviewer-lab claude auth login')):
                status = setup_status(provider)
                self.assertEqual(status['execution_environment'], 'container')
                self.assertEqual(status['login_command'], command)
                self.assertEqual(status['setup_command'], command)
                self.assertFalse(status['ready'])
                self.assertIsNone(status['connection']['authenticated'])
            api = setup_status('deepseek')
            self.assertEqual(api['execution_environment'], 'container')
            self.assertIsNone(api['login_command'])
            run.assert_not_called()
            http.assert_not_called()

    def test_container_flag_is_an_exact_opt_in_and_never_a_command(self):
        with patch('lab.providers._cli', return_value='/cli/codex'):
            for value in ('', '0', 'true', 'container', '1; unwanted-command'):
                with patch.dict('os.environ', {'REVIEWER_LAB_CONTAINER': value}, clear=True):
                    status = setup_status('codex')
                self.assertEqual(status['execution_environment'], 'local')
                self.assertEqual(status['login_command'], '/cli/codex login')

    def test_missing_container_cli_explains_rebuilding_the_image(self):
        with patch.dict('os.environ', {'REVIEWER_LAB_CONTAINER': '1'}, clear=True), \
                patch('lab.providers._cli', return_value=None):
            status = setup_status('claude')
        self.assertFalse(status['available'])
        self.assertIn('контейнере', status['message'])
        self.assertIn('Пересоберите образ', status['message'])

    def test_cached_container_connection_keeps_context_without_auth_details(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict('os.environ', {'REVIEWER_LAB_CONTAINER': '1'}, clear=True), \
                patch('lab.providers._cli', return_value='/cli/claude'), \
                patch('lab.ui.create_transport') as factory:
            factory.return_value.doctor.return_value = {'cli_version': 'fixture', 'auth_method': 'claude.ai'}
            app = Dashboard(tmp)
            app.check_provider({'provider': 'claude'})
            for _ in range(3):
                result = app.providers()
                status = next(p for p in result['providers'] if p['id'] == 'claude')
                self.assertEqual(status['execution_environment'], 'container')
                self.assertEqual(status['connection']['status'], 'ready')
                self.assertEqual(status['login_command'], 'docker compose exec reviewer-lab claude auth login')
            factory.return_value.doctor.assert_called_once_with()
            factory.return_value.call.assert_not_called()

    def test_presence_is_not_authenticated_readiness(self):
        with patch('lab.providers._cli', return_value='/cli/codex'), \
                patch('lab.providers.subprocess.run') as run:
            status = setup_status('codex')
        self.assertTrue(status['available'])
        self.assertFalse(status['ready'])
        self.assertEqual(status['connection']['status'], 'unchecked')
        self.assertIsNone(status['connection']['authenticated'])
        run.assert_not_called()

    def test_missing_cli_and_api_key_have_different_connection_kinds(self):
        with patch('lab.providers._cli', return_value=None), patch.dict('os.environ', {}, clear=True):
            cli, api = setup_status('claude'), setup_status('deepseek')
        self.assertEqual(cli['connection']['installed'], False)
        self.assertIsNone(api['connection']['installed'])
        self.assertEqual(api['connection']['status'], 'missing')
        self.assertEqual(api['models_source'], 'provider_catalog')

    def test_login_command_uses_detected_binary_and_quotes_shell_characters(self):
        binary = '/Applications/Some App/cli$(not-a-command)'
        with patch('lab.providers._cli', return_value=binary):
            status = setup_status('codex')
        self.assertEqual(shlex.split(status['login_command']), [binary, 'login'])
        self.assertEqual(status['setup_command'], status['login_command'])

    def test_api_key_presence_never_claims_validated_credentials(self):
        with patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'private-fixture-value'}), \
                patch('lab.providers.urllib.request.build_opener') as http:
            status = checked_status('deepseek', readiness={'provider': 'deepseek'})
        self.assertTrue(status['ready'])
        self.assertEqual(status['connection']['status'], 'configured')
        self.assertIsNone(status['connection']['authenticated'])
        self.assertFalse(status['connection']['model_access_verified'])
        self.assertNotIn('private-fixture-value', json.dumps(status))
        http.assert_not_called()

    def test_check_populates_cli_catalog_but_polling_only_reads_cache(self):
        with tempfile.TemporaryDirectory() as tmp, patch('lab.providers._cli', return_value='/cli/codex'), \
                patch('lab.ui.create_transport') as factory:
            app = Dashboard(tmp, read_only=True)
            transport = factory.return_value
            transport.doctor.return_value = {'authentication': 'ChatGPT', 'cli_version': 'codex-cli fixture'}
            transport.model_catalog.return_value = [
                {'slug': 'gpt-catalog-first', 'visibility': 'list'},
                {'slug': 'hidden-model', 'visibility': 'hide'},
                {'slug': 'gpt-catalog-first', 'visibility': 'list'},
                {'slug': 'gpt-catalog-second'},
            ]
            result = app.check_provider({'provider': 'codex'})
            for _ in range(5):
                self.assertEqual(app.providers(), result)
            selected = result['providers'][0]
            self.assertEqual(selected['models'], ['gpt-catalog-first', 'gpt-catalog-second'])
            self.assertEqual(selected['models_source'], 'codex_cli_catalog')
            self.assertEqual(selected['connection']['status'], 'ready')
            self.assertTrue(selected['connection']['authenticated'])
            self.assertFalse(selected['connection']['model_access_verified'])
            self.assertTrue(app.read_only)  # A connection check does not authorize a run.
            transport.doctor.assert_called_once_with(check_models=False)
            transport.model_catalog.assert_called_once_with()
            transport.call.assert_not_called()
            self.assertFalse((Path(tmp) / 'runs').exists())

    def test_logged_out_state_is_cached_without_account_or_raw_error(self):
        with tempfile.TemporaryDirectory() as tmp, patch('lab.providers._cli', return_value='/cli/claude'), \
                patch('lab.ui.create_transport') as factory:
            factory.return_value.doctor.side_effect = TransportError('Claude sign-in required; private@example.com')
            app = Dashboard(tmp)
            result = app.check_provider({'provider': 'claude'})
            status = next(p for p in result['providers'] if p['id'] == 'claude')
            self.assertEqual(status['connection']['status'], 'login_required')
            self.assertFalse(status['ready'])
            self.assertFalse(status['connection']['authenticated'])
            self.assertNotIn('private@example.com', json.dumps(result))
            app.providers()
            self.assertEqual(factory.return_value.doctor.call_count, 1)

    def test_catalog_failure_does_not_turn_confirmed_login_into_failure(self):
        with tempfile.TemporaryDirectory() as tmp, patch('lab.providers._cli', return_value='/cli/codex'), \
                patch('lab.ui.create_transport') as factory:
            factory.return_value.doctor.return_value = {'authentication': 'ChatGPT'}
            factory.return_value.model_catalog.side_effect = TransportError('catalog unavailable')
            app = Dashboard(tmp)
            selected = app.check_provider({'provider': 'codex'})['providers'][0]
            self.assertTrue(selected['ready'])
            self.assertEqual(selected['models_source'], 'suggested_defaults')
            self.assertIn('вручную', selected['models_note'])

    def test_presence_change_invalidates_cached_login(self):
        with tempfile.TemporaryDirectory() as tmp, patch('lab.providers._cli', return_value='/cli/claude'), \
                patch('lab.ui.create_transport') as factory:
            factory.return_value.doctor.return_value = {'cli_version': 'fixture'}
            app = Dashboard(tmp)
            app.check_provider({'provider': 'claude'})
            with patch('lab.providers._cli', return_value=None):
                status = next(p for p in app.providers()['providers'] if p['id'] == 'claude')
            self.assertEqual(status['connection']['status'], 'missing')
            self.assertFalse(status['ready'])

    def test_nonlocal_server_and_bad_payload_cannot_probe(self):
        with tempfile.TemporaryDirectory() as tmp, patch('lab.ui.create_transport') as factory:
            app = Dashboard(tmp)
            for config in ({}, {'provider': 'claude', 'command': 'anything'}, None):
                with self.assertRaises(ValueError):
                    app.check_provider(config)
            app.live_available = False
            with self.assertRaises(PermissionError):
                app.check_provider({'provider': 'claude'})
            factory.assert_not_called()

    def test_codex_catalog_shared_parser_rejects_malformed_records(self):
        with tempfile.TemporaryDirectory() as tmp, patch('lab.transport.shutil.which', return_value='/cli/codex'), \
                patch('lab.transport.subprocess.run') as run:
            transport = CodexTransport(tmp)
            for data in ({'models': []}, {'models': [None]}, {'models': [{'slug': 12}]}, []):
                run.return_value = subprocess.CompletedProcess([], 0, json.dumps(data), '')
                with self.assertRaises(TransportError):
                    transport.model_catalog()

    def test_connection_http_requires_token_and_origin(self):
        with tempfile.TemporaryDirectory() as tmp, patch('lab.ui.create_transport') as factory:
            server = make_server(tmp, port=0, read_only=True)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            factory.return_value.doctor.side_effect = TransportError('Claude sign-in required')
            try:
                origin = f'http://127.0.0.1:{server.server_port}'
                for token, actual_origin, expected in ((None, origin, 403),
                        (server.dashboard.token, 'https://other.example', 403),
                        (server.dashboard.token, origin, 200)):
                    headers = {'Content-Type': 'application/json', 'Origin': actual_origin}
                    if token:
                        headers['X-Lab-Token'] = token
                    client = http.client.HTTPConnection('127.0.0.1', server.server_port)
                    client.request('POST', '/api/providers/check', json.dumps({'provider': 'claude'}), headers)
                    response = client.getresponse()
                    self.assertEqual(response.status, expected)
                    response.read()
                    client.close()
                self.assertEqual(factory.return_value.doctor.call_count, 1)
                factory.return_value.call.assert_not_called()
            finally:
                server.shutdown()
                server.server_close()
                worker.join()


if __name__ == '__main__':
    unittest.main()
