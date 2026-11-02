"""Parallel benchmark hosts must identify their own local listener."""

import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI

from creatoros.evaluation.benchmark.run import wait_for_trial
from creatoros.evaluation.benchmark.server import open_trial_listener
from creatoros.evaluation.benchmark import manifest
from creatoros.evaluation.benchmark import run, report
from creatoros.web.server import StudioServer


class _ScenarioHandler(BaseHTTPRequestHandler):
    scenario = {}

    def do_GET(self):
        body = json.dumps(self.scenario).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class BenchmarkIsolationTests(unittest.TestCase):
    def test_freeze_requires_a_built_frontend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = root / 'web' / 'e2e' / 'live-benchmark-v2.mjs'
            runner.parent.mkdir(parents=True)
            runner.write_text('// browser runner', encoding='utf-8')
            with patch.object(manifest, 'PROJECT_ROOT', root):
                with self.assertRaisesRegex(ValueError, 'frontend'):
                    manifest._default_sources()

    def test_freeze_rejects_missing_frontend_entry_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = root / 'web' / 'e2e' / 'live-benchmark-v2.mjs'
            runner.parent.mkdir(parents=True)
            runner.write_text('// browser runner', encoding='utf-8')
            dist = root / 'web' / 'dist'
            dist.mkdir()
            (dist / 'index.html').write_text('<script type="module" src="/assets/missing.js"></script>', encoding='utf-8')
            with patch.object(manifest, 'PROJECT_ROOT', root):
                with self.assertRaisesRegex(ValueError, 'frontend'):
                    manifest._default_sources()

    def test_frontend_entry_must_be_reachable_by_studio_static_route(self):
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory)
            (dist / 'app.js').write_text('// exists but is not served', encoding='utf-8')
            (dist / 'index.html').write_text('<script type="module" src="/app.js"></script>', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'frontend'):
                manifest._frontend_entries(dist)

    def test_freeze_rejects_a_linked_source_before_resolving(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original, linked = root / 'original.py', root / 'linked.py'
            original.write_text('VALUE = 1', encoding='utf-8')
            try:
                linked.symlink_to(original)
            except OSError as error:
                self.skipTest(f'Creating symlinks is unavailable: {error}')
            with patch.object(manifest, 'PROJECT_ROOT', root):
                with self.assertRaisesRegex(ValueError, 'linked'):
                    manifest._relative(linked)

    def test_frontend_entry_rejects_a_linked_asset_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory) / 'dist'
            real = dist / 'assets' / 'real'
            real.mkdir(parents=True)
            (real / 'app.js').write_text('// real', encoding='utf-8')
            linked = dist / 'assets' / 'alias'
            try:
                linked.symlink_to(real, target_is_directory=True)
            except OSError as error:
                self.skipTest(f'Creating symlinks is unavailable: {error}')
            (dist / 'index.html').write_text('<script type="module" src="/assets/alias/app.js"></script>', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'linked'):
                manifest._frontend_entries(dist)

    def test_run_rejects_dataset_other_than_its_frozen_input(self):
        dataset = Path(__file__).resolve().parents[1] / 'docs/agent-eval/benchmark-v2/cases.json'
        original = json.loads(dataset.read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'fixture.py'
            source.write_text('VALUE = 1\n', encoding='utf-8')
            frozen_input = root / 'cases.json'
            frozen_input.write_text(json.dumps(original, ensure_ascii=False), encoding='utf-8')
            original['cases'][10]['steps'][0]['text'] = 'different unfrozen prompt'
            altered = root / 'alternative.json'
            altered.write_text(json.dumps(original, ensure_ascii=False), encoding='utf-8')
            target = root / 'data/agent-benchmark-v2/audit/manifest.json'
            with patch.object(manifest, 'PROJECT_ROOT', root), patch.object(run, 'REPO', root), \
                    patch.object(run, 'execute_case') as execute, patch.object(report, 'summarize_batch', return_value={}):
                manifest.freeze_manifest(target, frozen_input, pricing={'currency': 'USD'},
                    model_configuration={'retries': 0}, case_ids=['B11'], source_paths=[source])
                with patch.object(sys, 'argv', ['benchmark', 'run', '--batch', 'audit',
                                               '--dataset', str(altered), '--workers', '1']):
                    with self.assertRaisesRegex(ValueError, 'frozen dataset'):
                        run.main()
                execute.assert_not_called()

    def test_historical_report_does_not_require_current_source_to_match(self):
        dataset = Path(__file__).resolve().parents[1] / 'docs/agent-eval/benchmark-v2/cases.json'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'fixture.py'
            source.write_text('VALUE = 1\n', encoding='utf-8')
            frozen_input = root / 'cases.json'
            frozen_input.write_bytes(dataset.read_bytes())
            target = root / 'data/agent-benchmark-v2/history/manifest.json'
            with patch.object(manifest, 'PROJECT_ROOT', root), patch.object(run, 'REPO', root), \
                    patch.object(report, 'summarize_batch', return_value={}) as summarize:
                manifest.freeze_manifest(target, frozen_input, pricing={'currency': 'USD'},
                    model_configuration={'retries': 0}, case_ids=['B11'], source_paths=[source])
                source.write_text('VALUE = 2\n', encoding='utf-8')
                with patch.object(sys, 'argv', ['benchmark', 'report', '--batch', 'history']):
                    run.main()
                summarize.assert_called_once()

    def test_two_trials_bind_distinct_ports_before_starting(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / 'one'
            second = Path(directory) / 'two'
            first.mkdir()
            second.mkdir()
            config_a, socket_a = open_trial_listener(FastAPI(), first, 'B01', 0)
            try:
                config_b, socket_b = open_trial_listener(FastAPI(), second, 'B02', 0)
                try:
                    a = json.loads((first / 'server-address.json').read_text(encoding='utf-8'))
                    b = json.loads((second / 'server-address.json').read_text(encoding='utf-8'))
                    self.assertNotEqual(a['port'], b['port'])
                    self.assertEqual((a['case_id'], a['run_id']), ('B01', 'one'))
                    self.assertEqual((b['case_id'], b['run_id']), ('B02', 'two'))
                    self.assertEqual(a['port'], socket_a.getsockname()[1])
                    self.assertEqual(b['port'], socket_b.getsockname()[1])
                finally:
                    socket_b.close()
            finally:
                socket_a.close()

    def test_runner_rejects_a_different_trial_even_if_http_returns_200(self):
        handler = type('OtherTrial', (_ScenarioHandler,), {'scenario': {'case_id': 'B01', 'run_id': 'other'}})
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'expected'
                root.mkdir()
                (root / 'server-address.json').write_text(json.dumps({
                    'case_id': 'B01', 'run_id': 'expected', 'pid': 123,
                    'port': server.server_port,
                }), encoding='utf-8')
                process = SimpleNamespace(pid=123, poll=lambda: None)
                with self.assertRaisesRegex(RuntimeError, 'different trial'):
                    wait_for_trial(process, root, 'B01', timeout=2)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_runner_reaches_its_actual_bound_trial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'owned-run'
            root.mkdir()
            app = FastAPI()

            @app.get('/__benchmark__/scenario')
            def scenario():
                return {'case_id': 'B01', 'run_id': root.name}

            config, sock = open_trial_listener(app, root, 'B01', 0)
            server = StudioServer(config)
            thread = Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
            thread.start()
            try:
                process = SimpleNamespace(pid=os.getpid(), poll=lambda: None)
                port = wait_for_trial(process, root, 'B01', timeout=15)
                self.assertEqual(port, json.loads((root / 'server-address.json').read_text())['port'])
            finally:
                server.should_exit = True
                thread.join(timeout=15)
                sock.close()
            self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
