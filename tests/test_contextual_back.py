# SPDX-License-Identifier: GPL-3.0-only
"""使用模拟输入和输出测试退格映射，不发送真实按键。"""
import support
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

path = Path(os.environ.get('CONTEXTUAL_BACK_PATH', str(Path(__file__).resolve().parents[1] / 'src' / 'contextual_back.py')))
spec = importlib.util.spec_from_file_location('contextual_back', path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
E = m.ecodes


class Keyboard:
    def __init__(self):
        self.events = []
        self.closed = False
    def write(self, kind, code, value):
        self.events.append((kind, code, value))
    def write_event(self, event):
        self.write(event.type, event.code, event.value)
    def syn(self):
        pass
    def close(self):
        self.closed = True


class BackTests(unittest.TestCase):
    def setUp(self):
        self.keyboard = Keyboard()
        self.focus = True
        self.router = m.ContextualBackKeyboard(self.keyboard, lambda: self.focus,
                                               lambda _: None, monitor=False)

    def back(self, value):
        self.router.write_event(SimpleNamespace(type=E.EV_KEY, code=E.KEY_BACK, value=value))

    def test_codex_tap_is_one_backspace_pair(self):
        self.back(1); self.back(0)
        self.assertEqual(self.keyboard.events, [(E.EV_KEY, E.KEY_BACKSPACE, 1), (E.EV_KEY, E.KEY_BACKSPACE, 0)])

    def test_outside_codex_preserves_back_and_repeat(self):
        self.focus = False
        for value in [1, 2, 2, 0]: self.back(value)
        self.assertEqual(self.keyboard.events, [(E.EV_KEY, E.KEY_BACK, v) for v in [1, 2, 2, 0]])

    def test_codex_hold_preserves_repeats(self):
        for value in [1, 2, 2, 0]: self.back(value)
        self.assertEqual(self.keyboard.events, [(E.EV_KEY, E.KEY_BACKSPACE, v) for v in [1, 2, 2, 0]])

    def test_focus_change_releases_and_suppresses_until_keyup(self):
        self.back(1)
        self.focus = False
        self.router.check_focus()
        self.back(2); self.back(1)
        self.focus = True
        self.back(2); self.back(0)
        self.assertEqual(self.keyboard.events, [(E.EV_KEY, E.KEY_BACKSPACE, 1), (E.EV_KEY, E.KEY_BACKSPACE, 0)])
        self.back(1); self.back(0)
        self.assertEqual(len(self.keyboard.events), 4)

    def test_entering_codex_releases_previous_back_without_deleting(self):
        self.focus = False
        self.back(1)
        self.focus = True
        self.back(2); self.back(0)
        self.assertEqual(self.keyboard.events, [(E.EV_KEY, E.KEY_BACK, 1), (E.EV_KEY, E.KEY_BACK, 0)])

    def test_keyup_uses_original_code_even_if_focus_changed(self):
        self.back(1)
        self.focus = False
        self.back(0)
        self.assertEqual(self.keyboard.events[-1], (E.EV_KEY, E.KEY_BACKSPACE, 0))

    def test_disconnect_and_shutdown_release_held_key(self):
        self.back(1)
        self.router.release()
        self.assertEqual(self.keyboard.events[-1], (E.EV_KEY, E.KEY_BACKSPACE, 0))
        self.back(1)
        self.router.close()
        self.assertTrue(self.keyboard.closed)
        self.assertIsNone(self.router.output_code)
        self.assertEqual(self.keyboard.events[-1], (E.EV_KEY, E.KEY_BACKSPACE, 0))

    def test_duplicate_press_and_orphan_repeat_do_not_emit_keys(self):
        self.back(2); self.back(0)
        self.assertEqual(self.keyboard.events, [])
        self.back(1); self.back(1); self.back(0)
        self.assertEqual(len(self.keyboard.events), 2)

    def test_other_keys_and_sync_pass_through(self):
        events = [(E.EV_KEY, E.KEY_UP, 1), (E.EV_KEY, E.KEY_F5, 0), (E.EV_SYN, E.SYN_REPORT, 0)]
        for kind, code, value in events:
            self.router.write_event(SimpleNamespace(type=kind, code=code, value=value))
        self.assertEqual(self.keyboard.events, events)

    def test_extra_capability_does_not_modify_physical_capabilities(self):
        original = {E.EV_KEY: [E.KEY_BACK], E.EV_SYN: [0]}
        dev = SimpleNamespace(capabilities=lambda verbose=False: original, name='remote')
        proxy = m.BackspaceCapabilities(dev)
        self.assertIn(E.KEY_BACKSPACE, proxy.capabilities()[E.EV_KEY])
        self.assertEqual(original[E.EV_KEY], [E.KEY_BACK])
        self.assertEqual(proxy.name, 'remote')


if __name__ == '__main__':
    unittest.main()
