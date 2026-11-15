# SPDX-License-Identifier: GPL-3.0-only
"""用模拟状态和临时文件验证提示；不改蓝牙、音频或真实桌面通知。"""
import support
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import status_notifier as status
from remote_config import Settings


class GateTests(unittest.TestCase):
    def setUp(self):
        self.gate = status.NotificationGate(0)
        self.bad = status.HEALTH['disconnected']
        self.ok = status.HEALTH['ok']

    def send(self, health, now, success=True):
        candidate = self.gate.candidate(health, now)
        if candidate:
            self.gate.delivered(candidate, now, success)
        return candidate

    def test_healthy_start_is_silent_and_short_fault_does_not_emit_recovery(self):
        for t in (0, 30, 100):
            self.assertIsNone(self.send(self.ok, t))
        self.assertIsNone(self.send(self.bad, 101))
        self.assertIsNone(self.send(self.bad, 109))
        self.assertIsNone(self.send(self.ok, 110))
        self.assertIsNone(self.send(self.ok, 200))

    def test_startup_fault_waits_then_notifies_once(self):
        for t in (0, 10, 29):
            self.assertIsNone(self.send(self.bad, t))
        self.assertEqual(self.send(self.bad, 30), self.bad)
        for t in (60, 1000, 5000):
            self.assertIsNone(self.send(self.bad, t))

    def test_recovery_requires_stability_and_is_reported_once(self):
        self.send(self.bad, 0)
        self.send(self.bad, 30)
        self.assertIsNone(self.send(self.ok, 60))
        self.assertIsNone(self.send(self.ok, 64))
        self.assertEqual(self.send(self.ok, 65), self.ok)
        self.assertIsNone(self.send(self.ok, 100))

    def test_flapping_and_changing_faults_obey_global_cooldown(self):
        self.send(self.bad, 0)
        self.send(self.bad, 30)
        different = status.HEALTH['mic_missing']
        self.send(different, 31)
        self.assertIsNone(self.send(different, 45))
        self.assertEqual(self.send(different, 60), different)
        self.send(self.ok, 61)
        self.send(self.bad, 62)
        self.assertIsNone(self.send(self.bad, 75))
        self.assertEqual(self.send(self.bad, 90), self.bad)

    def test_delivery_failure_retries_with_cooldown(self):
        self.send(self.bad, 0)
        self.assertEqual(self.send(self.bad, 30, False), self.bad)
        self.assertIsNone(self.send(self.bad, 59))
        self.assertEqual(self.send(self.bad, 60), self.bad)
        self.assertIsNone(self.send(self.bad, 90))

    def test_unsent_fault_does_not_emit_a_misleading_recovery(self):
        self.send(self.bad, 0)
        self.send(self.bad, 30, False)
        self.assertIsNone(self.send(self.ok, 60))
        self.assertIsNone(self.send(self.ok, 65))

    def test_continuous_outage_is_not_hidden_by_changing_symptoms(self):
        self.send(self.ok, 30)
        self.assertIsNone(self.send(self.bad, 31))
        self.assertIsNone(self.send(status.HEALTH['bridge_down'], 36))
        mic = status.HEALTH['mic_missing']
        self.assertEqual(self.send(mic, 41), mic)

    def test_true_recovery_resets_the_outage_timer(self):
        self.send(self.bad, 30)
        self.send(self.ok, 35)
        self.assertIsNone(self.send(self.bad, 39))
        self.assertIsNone(self.send(self.bad, 45))
        self.assertEqual(self.send(self.bad, 49), self.bad)


def result(stdout='', code=0):
    return SimpleNamespace(stdout=stdout, returncode=code)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.config = Settings.from_dict({'remote_address': 'AA:BB:CC:DD:EE:FF'})

    def test_bluetooth_disconnected_is_distinct_from_failed_query(self):
        for output, expected in [('\tConnected: yes\n', True), (' Connected: no\n', False),
                                 ('Device unavailable\n', None), ('NotConnected: yes', None)]:
            with self.subTest(output=output):
                self.assertIs(status.bluetooth_connected(self.config.remote_address,
                    lambda *args: result(output)), expected)
        self.assertIsNone(status.bluetooth_connected(self.config.remote_address, lambda *args: None))
        self.assertIsNone(status.bluetooth_connected(self.config.remote_address, lambda *args: result('Connected: yes', 1)))

    def test_bridge_read_failure_and_inactive_are_distinct(self):
        for output, expected in [('active\n', True), ('activating\n', False), ('inactive\n', False), ('', None)]:
            self.assertIs(status.bridge_active(lambda *args: result(output)), expected)
        self.assertIsNone(status.bridge_active(lambda *args: None))

    def test_mic_exact_name_mute_zero_and_suspended_states(self):
        cases = [([], 'mic_missing'),
                 ([{'name': 'sayall-mic-sink.monitor'}], 'mic_missing'),
                 ([{'name': 'sayall-mic', 'mute': True}], 'mic_muted'),
                 ([{'name': 'sayall-mic', 'volume': {'mono': {'value': 0}}}], 'mic_zero'),
                 ([{'name': 'sayall-mic', 'state': 'SUSPENDED', 'mute': False,
                    'volume': {'mono': {'value': 65536}}}], 'ok')]
        for sources, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(status.microphone_state(lambda *args: result(json.dumps(sources))), expected)

    def test_bad_audio_query_is_not_reported_as_a_missing_mic(self):
        for value in (None, result('bad json'), result('{}'), result('[1]'), result('[]', 1)):
            self.assertEqual(status.microphone_state(lambda *args: value), 'audio_unknown')

    def test_query_commands_are_readonly_bounded_and_without_shell(self):
        with patch.object(status.subprocess, 'run', return_value=result('ok')) as run:
            status.run_readonly('pactl', '--format=json', 'list', 'sources')
        args, kwargs = run.call_args
        self.assertEqual(args[0], ('pactl', '--format=json', 'list', 'sources'))
        self.assertEqual(kwargs['timeout'], 3)
        self.assertNotIn('shell', kwargs)
        for exc in [FileNotFoundError(), subprocess.TimeoutExpired('pactl', 3), UnicodeError()]:
            with patch.object(status.subprocess, 'run', side_effect=exc):
                self.assertIsNone(status.run_readonly('pactl', 'info'))

    def test_hid_matches_identity_key_capability_and_read_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sys_input, dev_input = root / 'sys', root / 'dev'
            device = sys_input / 'event1/device'
            (device / 'id').mkdir(parents=True)
            (device / 'capabilities').mkdir()
            dev_input.mkdir()
            (dev_input / 'event1').touch()
            (device / 'uniq').write_text(self.config.remote_address.upper())
            (device / 'id/vendor').write_text('2717')
            (device / 'id/product').write_text('32b8')
            bits = struct.calcsize('L') * 8
            (device / 'capabilities/key').write_text('8000000000000000' if bits == 64 else '80000000 0')
            self.assertTrue(status.hid_available(self.config, sys_input, dev_input))
            with patch.object(status.os, 'access', return_value=False):
                self.assertFalse(status.hid_available(self.config, sys_input, dev_input))
            (device / 'uniq').write_text('11:22:33:44:55:66')
            self.assertFalse(status.hid_available(self.config, sys_input, dev_input))
            (device / 'uniq').write_text(self.config.remote_address)
            (device / 'capabilities/key').write_text('1')
            self.assertFalse(status.hid_available(self.config, sys_input, dev_input))
            (device / 'capabilities/key').unlink()
            self.assertFalse(status.hid_available(self.config, sys_input, dev_input))

    def test_socket_only_requests_status_and_handles_split_response(self):
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.recv.side_effect = [b'{"rea', b'dy":true}']
        with patch.object(status.socket, 'socket', return_value=connection):
            self.assertTrue(status.key_service_ready(Path('/tmp/example.sock')))
        connection.sendall.assert_called_once_with(b'{"action":"status"}')
        connection.settimeout.assert_called_once_with(0.5)
        for response in [b'[]', b'{"ready":false}', b'{"ready":1}', b'bad']:
            connection.recv.side_effect = [response, b'']
            with patch.object(status.socket, 'socket', return_value=connection):
                self.assertFalse(status.key_service_ready(Path('/tmp/example.sock')))
        with patch.object(status.socket, 'socket', side_effect=socket.timeout()):
            self.assertFalse(status.key_service_ready(Path('/tmp/example.sock')))

    def test_fault_priority_suppresses_downstream_symptoms(self):
        with patch.object(status, 'bluetooth_connected', return_value=False), \
                patch.object(status, 'microphone_state', side_effect=AssertionError('不应产生连锁故障')):
            self.assertEqual(status.collect_health(self.config).code, 'disconnected')

    def test_all_health_classifications_including_unknown_protection(self):
        defaults = dict(bluetooth_connected=True, hid_available=True, bridge_active=True,
                        microphone_state='ok', key_service_ready=True,
                        inspect_protection={'status': 'protected'})
        cases = [('bluetooth_connected', None, 'bluetooth_unknown'),
                 ('hid_available', False, 'hid_missing'),
                 ('bridge_active', False, 'bridge_down'),
                 ('bridge_active', None, 'bridge_unknown'),
                 ('microphone_state', 'mic_missing', 'mic_missing'),
                 ('key_service_ready', False, 'keys_down'),
                 ('inspect_protection', {'status': 'known_original'}, 'protection_original'),
                 ('inspect_protection', {'status': 'unknown'}, 'protection_unknown'),
                 ('inspect_protection', {'status': 'unavailable'}, 'protection_unknown'),
                 ('inspect_protection', {'status': 'protected'}, 'ok')]
        for name, value, expected in cases:
            with self.subTest(expected=expected), ExitStack() as stack:
                for key, answer in {**defaults, name: value}.items():
                    stack.enter_context(patch.object(status, key, return_value=answer))
                self.assertEqual(status.collect_health(self.config).code, expected)

    def test_once_is_readonly_and_does_not_send_notifications(self):
        for health, code in [(status.HEALTH['ok'], 0), (status.HEALTH['mic_missing'], 1)]:
            with patch.object(status, 'load_config', return_value=self.config), \
                    patch.object(status, 'collect_health', return_value=health), \
                    patch.object(status.DesktopNotifier, 'send', side_effect=AssertionError('只读检查不得发送通知')), \
                    redirect_stdout(io.StringIO()) as out:
                self.assertEqual(status.main(['--once']), code)
                self.assertEqual(json.loads(out.getvalue())['code'], health.code)


class NotificationTests(unittest.TestCase):
    def test_notification_reuses_id_suppresses_sound_and_never_waits_for_user(self):
        run = Mock(return_value=result('42\n'))
        notifier = status.DesktopNotifier(run)
        self.assertTrue(notifier.send(status.HEALTH['disconnected']))
        self.assertTrue(notifier.send(status.HEALTH['ok']))
        first, second = [call.args[0] for call in run.call_args_list]
        self.assertNotIn('--replace-id', first)
        self.assertEqual(second[second.index('--replace-id') + 1], '42')
        self.assertIn('--hint=boolean:suppress-sound:true', first)
        self.assertNotIn('--wait', first)
        self.assertNotIn('shell', run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs['timeout'], 3)

    def test_notification_errors_are_nonfatal(self):
        for exc in [FileNotFoundError(), subprocess.TimeoutExpired('notify-send', 3), UnicodeError()]:
            self.assertFalse(status.DesktopNotifier(Mock(side_effect=exc)).send(status.HEALTH['ok']))
        self.assertFalse(status.DesktopNotifier(Mock(return_value=result('', 1))).send(status.HEALTH['ok']))

    def test_monitor_observes_fault_then_recovery_without_duplicate_delivery(self):
        stop = Mock()
        stop.is_set.side_effect = [False] * 6 + [True]
        notifier = Mock()
        notifier.send.return_value = True
        states = ['disconnected', 'disconnected', 'disconnected', 'ok', 'ok', 'ok']
        with patch.object(status, 'collect_health', side_effect=[status.HEALTH[x] for x in states]), \
                redirect_stdout(io.StringIO()):
            status.monitor(None, Path('/tmp/example.sock'), stop, notifier,
                           clock=Mock(side_effect=[0, 0, 30, 60, 65, 70, 100]))
        self.assertEqual([call.args[0].code for call in notifier.send.call_args_list], ['disconnected', 'ok'])
