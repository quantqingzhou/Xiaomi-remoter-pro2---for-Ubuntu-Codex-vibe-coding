# SPDX-License-Identifier: GPL-3.0-only
"""后台控制仅操作模拟 systemctl；覆盖部分失败后的重试与用户偏好。"""
import support
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

import service_controls as C


class ServiceControlTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.keys = 'xiaomi-codex-keys.service'
        self.active = {u: True for u in (C.BRIDGE, C.NOTIFIER, self.keys)}
        self.enabled = dict(self.active)
        for context in (patch.object(C, 'state_path', return_value=Path(temp.name) / 'controls.json'),
                        patch.object(C, 'key_unit', return_value=self.keys),
                        patch.object(C.settings_status, 'service_active', side_effect=self.active.get),
                        patch.object(C, 'enabled', side_effect=lambda u=C.BRIDGE: self.enabled.get(u))):
            context.start(); self.addCleanup(context.stop)
        context = patch.object(C, 'command', side_effect=self.change)
        self.command = context.start(); self.addCleanup(context.stop)

    def change(self, action, *units):
        states = self.active if action in ('start', 'stop') else self.enabled
        for unit in units:
            states[unit] = action in ('start', 'enable')

    def test_pause_orders_notification_stop_before_bridge_without_disabling(self):
        C.pause()
        self.assertEqual(self.command.call_args_list, [call('stop', C.NOTIFIER), call('stop', C.BRIDGE, self.keys)])
        self.assertTrue(C.read_preferences()['resume_notifier'])
        self.assertTrue(self.enabled[C.BRIDGE])
        self.assertFalse(self.active[C.BRIDGE])

    def test_resume_after_window_reopened_restores_previous_notifier(self):
        C.pause(); self.command.reset_mock()
        self.enabled[C.NOTIFIER] = False
        C.resume()
        self.assertEqual(self.command.call_args_list, [call('start', self.keys, C.BRIDGE), call('start', C.NOTIFIER)])
        self.assertFalse(C.read_preferences()['paused'])
        self.assertNotIn('resume_notifier', C.read_preferences())

    def test_resume_preserves_manually_stopped_notifier(self):
        self.active[C.NOTIFIER] = False
        C.pause(); self.command.reset_mock(); C.resume()
        self.command.assert_called_once_with('start', self.keys, C.BRIDGE)
        self.assertFalse(self.active[C.NOTIFIER])

    def test_failed_pause_retry_and_external_restart_preserve_notification(self):
        for partial in (True, False):
            with self.subTest(partial=partial):
                C.save_preferences({})
                self.active.update({u: True for u in self.active})
                def first(action, *units):
                    if partial and C.BRIDGE in units:
                        raise RuntimeError('桥接未能停止')
                    self.change(action, *units)
                self.command.side_effect = first
                if partial:
                    with self.assertRaisesRegex(RuntimeError, '暂停尚未完成'): C.pause()
                else:
                    C.pause(); self.active[C.BRIDGE] = True
                self.command.side_effect = self.change
                C.pause(); C.resume()
                self.assertTrue(self.active[C.NOTIFIER])

    def test_unknown_notification_state_never_claims_success(self):
        self.enabled[C.NOTIFIER] = None
        with self.assertRaisesRegex(RuntimeError, '无法确认'): C.set_autostart(False)
        self.active[C.NOTIFIER] = None
        with self.assertRaisesRegex(RuntimeError, '无法确认'): C.pause()
        self.command.assert_not_called()
        self.assertEqual(C.read_preferences(), {})

    def test_autostart_does_not_change_current_running_state(self):
        C.set_autostart(False); C.set_autostart(True)
        self.assertEqual(self.command.call_args_list, [call('disable', C.BRIDGE, C.NOTIFIER), call('enable', C.BRIDGE, C.NOTIFIER)])
        self.assertTrue(all(self.active.values()))

    def test_completed_autostart_cycle_respects_later_manual_notification_disable(self):
        C.set_autostart(False); C.set_autostart(True)
        self.enabled[C.NOTIFIER] = False
        self.command.reset_mock()
        C.set_autostart(False); C.set_autostart(True)
        self.assertEqual(self.command.call_args_list, [call('disable', C.BRIDGE), call('enable', C.BRIDGE)])
        self.assertFalse(self.enabled[C.NOTIFIER])

    def test_partial_autostart_disable_retry_restores_original_notification(self):
        def partial(action, *units):
            self.enabled[C.NOTIFIER] = False
            raise RuntimeError('桥接自启关闭失败')
        self.command.side_effect = partial
        with self.assertRaises(RuntimeError): C.set_autostart(False)
        self.command.side_effect = self.change
        C.set_autostart(False); C.set_autostart(True)
        self.assertTrue(self.enabled[C.NOTIFIER])

    def test_successful_command_with_unchanged_status_is_not_success(self):
        self.command.side_effect = lambda *_: None
        with self.assertRaisesRegex(RuntimeError, '状态尚未达到预期'): C.set_autostart(False)
        self.assertTrue(C.read_preferences()['autostart_disabled'])

    def test_failed_resume_keeps_pause_record(self):
        C.pause(); self.command.side_effect = RuntimeError('failure')
        with self.assertRaises(RuntimeError): C.resume()
        self.assertTrue(C.read_preferences()['paused'])


class ServiceCommandTests(unittest.TestCase):
    def test_command_is_allowlisted_and_timeout_is_understandable(self):
        with patch.object(C.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stderr='')) as run:
            C.command('stop', C.BRIDGE)
            self.assertEqual(run.call_args.args[0], ['systemctl', '--user', 'stop', C.BRIDGE])
            with self.assertRaises(ValueError): C.command('stop', 'pipewire.service')
            with self.assertRaises(ValueError): C.command('restart', 'bluetooth.service')
            self.assertEqual(run.call_count, 1)
            run.side_effect = subprocess.TimeoutExpired(['systemctl'], 20)
            with self.assertRaisesRegex(RuntimeError, '系统可能仍在处理'): C.command('stop', C.BRIDGE)

    def test_absent_optional_unit_is_distinct_from_query_failure(self):
        reply = lambda text: SimpleNamespace(returncode=0, stdout=text)
        with patch.object(C.settings_status.probes, 'run_readonly', side_effect=[reply(''), reply('not-found')]):
            self.assertIs(C.enabled(C.NOTIFIER), False)
        with patch.object(C.settings_status.probes, 'run_readonly', return_value=None):
            self.assertIsNone(C.enabled(C.NOTIFIER))
