# SPDX-License-Identifier: GPL-3.0-only
"""设置界面只读诊断测试，不访问真实设备或日志。"""
import support
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import settings_status as S
from remote_config import Settings


class StatusPanelTests(unittest.TestCase):
    def test_bluez_and_hyphenated_addresses_are_redacted(self):
        for value in ('AA:BB:CC:DD:EE:FF', 'aa-bb-cc-dd-ee-ff', '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF'):
            self.assertIn('<蓝牙地址>', S.redact(value))
            self.assertNotIn('AA_BB', S.redact(value))

    def collect(self, connected=True, mic='ok'):
        config = Settings.from_dict({'remote_address': 'AA:BB:CC:DD:EE:FF'})
        with ExitStack() as stack:
            for target, name, value in [(S, 'load_config', config), (S, 'service_active', True),
                    (S.probes, 'bluetooth_connected', connected), (S.probes, 'hid_available', True),
                    (S.probes, 'bridge_active', True), (S.probes, 'microphone_state', mic),
                    (S.probes, 'key_service_ready', True),
                    (S.probes, 'inspect_protection', {'status': 'protected'})]:
                stack.enter_context(patch.object(target, name, return_value=value))
            return S.collect_status()['items']

    def test_unknown_bluetooth_is_distinct_from_disconnected(self):
        self.assertEqual(self.collect(None)['bluetooth'][0], 'unknown')
        self.assertEqual(self.collect(False)['bluetooth'], ('warning', '未连接'))
        self.assertEqual(self.collect(True)['bluetooth'], ('ok', '已连接'))

    def test_connected_does_not_hide_microphone_fault(self):
        items = self.collect(True, 'mic_muted')
        self.assertEqual(items['bluetooth'][0], 'ok')
        self.assertEqual(items['mic'], ('warning', '已静音'))

    def test_unconfigured_window_does_not_probe_any_device(self):
        with patch.object(S, 'panel_config', side_effect=ValueError('missing')), \
                patch.object(S.probes, 'bluetooth_connected') as bluetooth:
            self.assertEqual(S.collect_status()['items']['bluetooth'][0], 'unknown')
            bluetooth.assert_not_called()

    def test_legacy_config_is_read_without_copying_or_changing_it(self):
        with tempfile.TemporaryDirectory() as folder:
            legacy = Path(folder) / 'xiaomi-codex-status/config.json'
            legacy.parent.mkdir()
            content = json.dumps({'remote_address': 'AA:BB:CC:DD:EE:FF'})
            legacy.write_text(content)
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': folder}, clear=True), \
                    patch.object(S, 'load_config', side_effect=ValueError('missing')):
                config, socket = S.panel_config()
            self.assertEqual(config.remote_address, 'aa:bb:cc:dd:ee:ff')
            self.assertEqual(socket, S.probes.RUNTIME / 'xiaomi-codex/keys.sock')
            self.assertEqual(legacy.read_text(), content)
            self.assertFalse((Path(folder) / 'xiaomi-codex-remote').exists())

    def test_explicit_config_failure_does_not_fall_back_to_another_device(self):
        with patch.dict(os.environ, {'XIAOMI_CODEX_CONFIG': '/nonexistent/explicit.json'}), \
                patch.object(S, 'load_config', side_effect=ValueError('missing')):
            with self.assertRaises(ValueError):
                S.panel_config()

    def test_logs_include_faults_from_info_priority_and_redact_identity(self):
        records = [
            {'MESSAGE': json.dumps({'code': 'disconnected', 'title': '连接断开', 'body': 'AA:BB:CC:DD:EE:FF /home/demo/project'}), 'PRIORITY': '6'},
            {'MESSAGE': json.dumps({'code': 'ok', 'title': '已恢复', 'body': '正常'}), 'PRIORITY': '6'},
            {'MESSAGE': 'normal status', 'PRIORITY': '6'},
            {'MESSAGE': 'driver unavailable', 'PRIORITY': '6'},
        ]
        raw = '\n'.join(json.dumps(record) for record in records)
        errors = S.format_logs(raw)
        self.assertIn('连接断开', errors)
        self.assertIn('driver unavailable', errors)
        self.assertNotIn('已恢复', errors)
        self.assertNotIn('AA:BB', errors)
        self.assertNotIn('/home/demo', errors)
        self.assertIn('已恢复', S.format_logs(raw, False))

    def test_log_query_failure_is_not_reported_as_no_errors(self):
        with patch.object(S.probes, 'run_readonly', return_value=None):
            self.assertIn('无法读取', S.read_logs())
        with patch.object(S.probes, 'run_readonly', return_value=SimpleNamespace(returncode=0, stdout='')) as run:
            self.assertIn('没有找到错误', S.read_logs())
            self.assertEqual(run.call_args.args[0], 'journalctl')
            self.assertNotIn('sudo', run.call_args.args)


if __name__ == '__main__':
    unittest.main()
