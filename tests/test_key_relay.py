# SPDX-License-Identifier: GPL-3.0-only
"""遥控器重连与乱序按键命令的回归测试。

使用模拟设备和模拟键盘，不打开真实硬件，也不注入按键。
"""
import support
import errno
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

path = Path(os.environ.get('KEY_RELAY_PATH', str(Path(__file__).resolve().parents[1] / 'src' / 'codex-ptt-key.py')))
spec = importlib.util.spec_from_file_location('remote_keys', path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
REMOTE_ADDRESS = 'aa:bb:cc:dd:ee:ff'
F5, ENTER = m.evdev.ecodes.KEY_F5, m.evdev.ecodes.KEY_ENTER


def remote_state(*args, **kwargs):
    return m.RemoteState(*args, address=REMOTE_ADDRESS, **kwargs)


class Device:
    def __init__(self, keys=None, active=None, address=REMOTE_ADDRESS):
        self.uniq = address
        self.info = SimpleNamespace(vendor=0x2717, product=0x32B8)
        self.keys = [F5] if keys is None else keys
        self.active = [F5] if active is None else active
        self.closed = False

    def capabilities(self):
        return {m.evdev.ecodes.EV_KEY: self.keys}

    def active_keys(self):
        if isinstance(self.active, BaseException):
            raise self.active
        return self.active

    def close(self):
        self.closed = True


class RemoteTests(unittest.TestCase):
    def test_native_evdev_system_error_reopens_in_same_call(self):
        old = Device(active=SystemError('ioctl_EVIOCG_bits returned NULL without setting an exception'))
        new = Device()
        state = remote_state(lambda: ['new'], lambda path: new)
        state.device = old
        self.assertIs(state.pressed(), True)
        self.assertTrue(old.closed)
        self.assertIs(state.device, new)

    def test_native_evdev_system_error_without_replacement_is_unavailable(self):
        old = Device(active=SystemError('设备已移除'))
        state = remote_state(lambda: [], lambda path: None)
        state.device = old
        self.assertIsNone(state.pressed())
        self.assertTrue(old.closed)

    def test_first_press_after_disconnect_reopens_in_same_call(self):
        old = Device(active=OSError(errno.ENODEV, 'disconnected'))
        new = Device()
        state = remote_state(lambda: ['new'], lambda path: new)
        state.device = old
        self.assertIs(state.pressed(), True)
        self.assertTrue(old.closed)
        self.assertIs(state.device, new)

    def test_matching_non_keyboard_node_is_skipped(self):
        nodes = {'consumer': Device(keys=[ENTER]), 'keyboard': Device()}
        state = remote_state(lambda: list(nodes), nodes.__getitem__)
        self.assertIs(state.pressed(), True)
        self.assertTrue(nodes['consumer'].closed)
        self.assertIs(state.device, nodes['keyboard'])

    def test_inaccessible_candidate_does_not_hide_remote(self):
        good = Device(active=[])
        def opener(path):
            if path == 'denied':
                raise PermissionError('无法访问无关输入节点')
            return good
        state = remote_state(lambda: ['denied', 'remote'], opener)
        self.assertIs(state.pressed(), False)

    def test_wrong_address_is_not_used(self):
        wrong = Device(address='00:00:00:00:00:00')
        state = remote_state(lambda: ['wrong'], lambda path: wrong)
        self.assertIsNone(state.pressed())
        self.assertTrue(wrong.closed)

    def test_unavailable_state_is_distinct_from_released(self):
        state = remote_state(lambda: [], lambda path: None)
        self.assertIsNone(state.pressed())
        disconnected = []
        def opener(path):
            dev = Device(active=OSError(errno.ENODEV, '尚未就绪'))
            disconnected.append(dev)
            return dev
        state = remote_state(lambda: ['gone'], opener)
        self.assertIsNone(state.pressed())
        self.assertEqual(len(disconnected), 2)
        self.assertTrue(all(d.closed for d in disconnected))


class Keyboard:
    def __init__(self):
        self.events = []
    def write(self, event_type, code, value):
        self.events.append((code, value))
    def syn(self):
        pass


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.keyboard = Keyboard()
        self.focus = True
        self.remote = True
        self.now = 0.0
        self.audio_safe = True
        self.relay = m.KeyRelay(self.keyboard, lambda: self.focus,
                               lambda: self.remote, lambda: self.now,
                               playback_safe=lambda: self.audio_safe)

    def start(self, stamp=100):
        self.relay.apply('start', stamp)
        self.assertTrue(self.relay.held)

    def test_normal_hold_release_and_confirm(self):
        self.start()
        self.remote = False
        self.relay.apply('stop', 200)
        self.relay.apply('send', 300)
        self.assertEqual(self.keyboard.events, [(F5, 1), (F5, 0), (ENTER, 1), (ENTER, 0)])

    def test_missing_playback_protection_blocks_dictation(self):
        self.audio_safe = False
        self.assertIn('播放保护', self.relay.apply('start', 100))
        self.assertFalse(self.relay.held)
        self.assertEqual(self.keyboard.events, [])

    def test_client_update_during_recording_releases_f5(self):
        self.start()
        self.audio_safe = False
        self.assertIn('播放保护失效', self.relay.watchdog())
        self.assertFalse(self.relay.held)
        self.assertEqual(self.keyboard.events, [(F5, 1), (F5, 0)])

    def test_omitted_policy_never_starts_dictation(self):
        relay = m.KeyRelay(self.keyboard, lambda: True, lambda: True)
        self.assertIn('播放保护', relay.apply('start', 100))
        self.assertEqual(self.keyboard.events, [])

    def test_quick_tap_has_no_late_keydown(self):
        self.remote = False
        self.relay.apply('stop', 200)
        self.relay.apply('start', 100)
        self.assertFalse(self.relay.held)
        self.assertEqual(self.keyboard.events, [])

    def test_stop_with_older_timestamp_still_releases_real_keyup(self):
        self.start(stamp=300)
        self.remote = False
        self.relay.apply('stop', 200)
        self.assertFalse(self.relay.held)
        self.assertEqual(self.keyboard.events, [(F5, 1), (F5, 0)])

    def test_delayed_stop_does_not_interrupt_new_physical_press(self):
        self.start(stamp=300)
        self.relay.apply('stop', 400)
        self.assertTrue(self.relay.held)
        self.assertEqual(self.keyboard.events, [(F5, 1)])
        self.remote = False
        self.relay.apply('stop', 500)
        self.assertFalse(self.relay.held)

    def test_duplicate_commands_do_not_duplicate_key_events(self):
        self.start()
        self.relay.apply('start', 101)
        self.remote = False
        self.relay.apply('stop', 200)
        self.relay.apply('stop', 201)
        self.assertEqual(self.keyboard.events, [(F5, 1), (F5, 0)])

    def test_missing_stop_is_released_after_grace(self):
        self.start()
        self.remote = False
        self.assertIsNone(self.relay.watchdog())
        self.now = 1.4
        self.assertIsNone(self.relay.watchdog())
        self.assertTrue(self.relay.held)
        self.now = 1.6
        self.assertIsNotNone(self.relay.watchdog())
        self.assertFalse(self.relay.held)
        self.assertEqual(self.keyboard.events, [(F5, 1), (F5, 0)])

    def test_transient_release_or_read_error_does_not_end_recording(self):
        self.start()
        self.remote = None
        self.relay.watchdog()
        self.now = 0.3
        self.remote = True
        self.assertIsNone(self.relay.watchdog())
        self.now = 1.7
        self.assertIsNone(self.relay.watchdog())
        self.assertTrue(self.relay.held)

    def test_disconnect_without_stop_is_released_after_grace(self):
        self.start()
        self.remote = None
        self.relay.watchdog()
        self.now = 1.6
        self.assertIsNotNone(self.relay.watchdog())
        self.assertFalse(self.relay.held)

    def test_hard_timeout_when_remote_stays_down(self):
        self.start()
        self.now = 90
        self.assertIsNotNone(self.relay.watchdog())
        self.assertFalse(self.relay.held)

    def test_start_needs_focus_and_known_pressed_state(self):
        self.focus = False
        self.relay.apply('start', 100)
        self.focus = True
        self.remote = None
        self.relay.apply('start', 200)
        self.assertEqual(self.keyboard.events, [])

    def test_enter_blocked_while_dictating_and_outside_codex(self):
        self.start()
        self.relay.apply('send', 101)
        self.remote = False
        self.relay.apply('stop', 200)
        self.focus = False
        self.relay.apply('send', 300)
        self.assertEqual(self.keyboard.events, [(F5, 1), (F5, 0)])

    def test_shutdown_release_is_unconditional(self):
        self.start()
        self.relay.release()
        self.assertFalse(self.relay.held)
        self.assertEqual(self.keyboard.events, [(F5, 1), (F5, 0)])


class FocusTests(unittest.TestCase):
    def test_window_class_match_is_exact_and_case_insensitive(self):
        settings=SimpleNamespace(codex_wm_classes=('chatgpt',))
        for window_class,expected in [('Chatgpt',True),('SomeChatgptBrowser',False),('Firefox',False)]:
            with self.subTest(window_class=window_class),patch.object(m,'load_config',return_value=settings),patch.object(m.subprocess,'check_output',side_effect=[
                '_NET_ACTIVE_WINDOW(WINDOW): window id # 0x123',f'WM_CLASS(STRING) = "instance", "{window_class}"']):
                self.assertEqual(m.focused_window_is_codex(),expected)

    def test_unavailable_focus_is_not_treated_as_codex(self):
        with patch.object(m.subprocess,'check_output',side_effect=OSError('没有可用显示')):
            self.assertFalse(m.focused_window_is_codex())


if __name__ == '__main__':
    unittest.main()
