# SPDX-License-Identifier: GPL-3.0-only
"""Claude 仅扩展听写、发送与退格；不打开麦克风或操作真实窗口。"""
import support
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from evdev import ecodes as E
from keymap_config import MappingStore
from remote_config import Settings

path = Path(os.environ.get('KEY_RELAY_PATH', str(Path(__file__).resolve().parents[1] / 'src/codex-ptt-key.py')))
spec = importlib.util.spec_from_file_location('claude_key_relay', path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Keyboard:
    def __init__(self):
        self.events = []
        self.fail = None
    def write(self, kind, code, value):
        if (code, value) == self.fail:
            raise OSError('模拟设备写入失败')
        self.events.append((code, value))
    def syn(self):
        pass
    def close(self):
        pass


class ClaudeRelayTests(unittest.TestCase):
    def setUp(self):
        self.keyboard = Keyboard()
        self.context = 'claude'
        self.pressed = True
        self.now = 100.0
        self.relay = m.KeyRelay(self.keyboard, lambda: self.context, lambda: self.pressed,
                               clock=lambda: self.now, playback_safe=lambda: False)

    def test_hold_combo_and_reverse_release_without_duplicate_start(self):
        self.assertIn('Ctrl+D', self.relay.apply('start', 1))
        self.relay.apply('start', 2)
        self.assertEqual(self.keyboard.events, [(E.KEY_LEFTCTRL, 1), (E.KEY_D, 1)])
        self.pressed = False
        self.relay.apply('stop', 3)
        self.relay.apply('stop', 4)
        self.assertEqual(self.keyboard.events, [(E.KEY_LEFTCTRL, 1), (E.KEY_D, 1),
                                               (E.KEY_D, 0), (E.KEY_LEFTCTRL, 0)])
        self.assertFalse(self.relay.held)

    def test_confirm_is_enter_and_waits_until_dictation_has_ended(self):
        self.relay.apply('start', 1)
        self.relay.apply('send', 2)
        self.assertNotIn((E.KEY_ENTER, 1), self.keyboard.events)
        self.pressed = False
        self.relay.apply('stop', 3)
        self.relay.mappings = SimpleNamespace(refresh=lambda: None,
            codes=lambda *args: (_ for _ in ()).throw(AssertionError('不能借用其他软件的确认映射')))
        self.relay.apply('send', 4)
        self.assertEqual(self.keyboard.events[-2:], [(E.KEY_ENTER, 1), (E.KEY_ENTER, 0)])

    def test_focus_change_releases_both_keys_and_requires_physical_release(self):
        self.relay.apply('start', 1)
        self.context = 'codex'
        self.relay.watchdog()
        self.assertEqual(self.keyboard.events[-2:], [(E.KEY_D, 0), (E.KEY_LEFTCTRL, 0)])
        self.assertTrue(self.relay.wait_for_release)
        before = list(self.keyboard.events)
        self.relay.apply('start', 2)
        self.assertEqual(self.keyboard.events, before)
        self.pressed = False
        self.relay.watchdog()
        self.assertFalse(self.relay.wait_for_release)

    def test_lost_stop_and_disconnect_release_entire_combo(self):
        self.relay.apply('start', 1)
        self.pressed = None
        self.relay.watchdog()
        self.now += m.RELEASE_GRACE_SECONDS + 0.1
        self.relay.watchdog()
        self.assertFalse(self.relay.held)
        self.assertEqual(self.keyboard.events[-2:], [(E.KEY_D, 0), (E.KEY_LEFTCTRL, 0)])

    def test_delayed_start_after_quick_tap_is_ignored(self):
        self.pressed = False
        self.relay.apply('start', 1)
        self.assertEqual(self.keyboard.events, [])

    def test_press_failure_releases_modifier(self):
        self.keyboard.fail = (E.KEY_D, 1)
        with self.assertRaises(OSError):
            self.relay.apply('start', 1)
        self.assertIn((E.KEY_LEFTCTRL, 0), self.keyboard.events)
        self.assertFalse(self.relay.held)

    def test_release_failure_still_releases_modifier_and_clears_state(self):
        self.relay.apply('start', 1)
        self.keyboard.fail = (E.KEY_D, 0)
        with self.assertRaises(OSError):
            self.relay.release()
        self.assertEqual(self.keyboard.events[-1], (E.KEY_LEFTCTRL, 0))
        self.assertFalse(self.relay.held)

    def test_timeout_releases_combo(self):
        self.relay.apply('start', 1)
        self.now += m.MAX_HOLD_SECONDS + 1
        self.relay.watchdog()
        self.assertEqual(self.keyboard.events[-2:], [(E.KEY_D, 0), (E.KEY_LEFTCTRL, 0)])

    def test_unknown_focus_never_receives_claude_combo_or_enter(self):
        self.context = None
        self.relay.apply('start', 1)
        self.relay.apply('send', 2)
        self.assertEqual(self.keyboard.events, [])

    def test_claude_backspace_does_not_overwrite_other_profile(self):
        import tempfile
        from mapping_keyboard import MappingKeyboard
        with tempfile.TemporaryDirectory() as folder:
            store = MappingStore(Path(folder) / 'keymap.json')
            store.refresh()
            store.data['profiles']['other']['back'] = 'Ctrl+Z'
            self.assertEqual(store.codes('back', 'claude', E.KEY_BACK), (E.KEY_BACKSPACE,))
            self.assertEqual(store.codes('back', 'other', E.KEY_BACK), (E.KEY_LEFTCTRL, E.KEY_Z))
            self.assertEqual(MappingKeyboard.context(SimpleNamespace(focus=lambda: 'claude')), 'claude')

    def test_focus_change_stops_claude_backspace_repeat(self):
        import tempfile
        from mapping_keyboard import MappingKeyboard
        with tempfile.TemporaryDirectory() as folder:
            store = MappingStore(Path(folder) / 'keymap.json')
            keyboard = MappingKeyboard(self.keyboard, lambda: self.context, store, monitor=False, log=lambda _: None)
            try:
                keyboard.write_event(SimpleNamespace(type=E.EV_KEY, code=E.KEY_BACK, value=1))
                self.context = None
                keyboard.check_focus()
                keyboard.write_event(SimpleNamespace(type=E.EV_KEY, code=E.KEY_BACK, value=2))
                keyboard.write_event(SimpleNamespace(type=E.EV_KEY, code=E.KEY_BACK, value=0))
                self.assertEqual(self.keyboard.events, [(E.KEY_BACKSPACE, 1), (E.KEY_BACKSPACE, 0)])
            finally:
                keyboard.close()


class ClaudeConfigurationTests(unittest.TestCase):
    def test_optional_and_normalized_but_distinct_from_other_apps(self):
        data = {'remote_address': 'aa:bb:cc:dd:ee:ff'}
        self.assertEqual(Settings.from_dict(data).claude_wm_classes, ())
        cfg = Settings.from_dict({**data, 'claude_wm_classes': [' com.anthropic.Claude ']})
        self.assertEqual(cfg.to_dict()['claude_wm_classes'], ['com.anthropic.claude'])
        for value in ('Claude', [None], [''], ['chatgpt']):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Settings.from_dict({**data, 'claude_wm_classes': value})
        with self.assertRaises(ValueError):
            Settings.from_dict({**data, 'claude_wm_classes': ['claude'], 'wechat_wm_classes': ['Claude']})

    def test_focus_exact_match_and_native_wayland_unknown(self):
        for window_class, expected in [('com.anthropic.Claude', 'claude'), ('NotClaude', None)]:
            with self.subTest(window_class=window_class), patch.object(m.subprocess, 'check_output', side_effect=[
                    '_NET_ACTIVE_WINDOW(WINDOW): window id # 0x123',
                    f'WM_CLASS(STRING) = "instance", "{window_class}"']):
                if hasattr(m, 'load_config'):
                    config = SimpleNamespace(codex_wm_classes=('chatgpt',), wechat_wm_classes=(),
                                             claude_wm_classes=('com.anthropic.claude',))
                    with patch.object(m, 'load_config', return_value=config):
                        self.assertEqual(m.focused_window_context(), expected)
                else:
                    self.assertEqual(m.focused_window_context(), expected)
        with patch.object(m.subprocess, 'check_output', side_effect=OSError('无 X11 活动窗口')):
            self.assertIsNone(m.focused_window_context())


if __name__ == '__main__':
    unittest.main()
