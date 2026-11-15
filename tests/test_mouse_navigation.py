# SPDX-License-Identifier: GPL-3.0-only
"""鼠标状态机与实际 SayAll 路由边界；所有输入、时间和状态条均为隔离替身。"""
import support
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from evdev import ecodes as E
import keymap_config as C
from mouse_navigation import MouseNavigation
from mapping_keyboard import MappingKeyboard
from key_detection import EventDevice


def event(code, value):
    return SimpleNamespace(type=E.EV_KEY, code=code, value=value)


class Output:
    def __init__(self): self.events, self.closed = [], False
    def write(self, kind, code, value): self.events.append((kind, code, value))
    def write_event(self, e): self.write(e.type, e.code, e.value)
    def syn(self): pass
    def close(self): self.closed = True


class Indicator:
    def __init__(self): self.states, self.available = [], True
    def update(self, active, context, hint=''):
        self.states.append((active, context, hint))
        return self.available
    def close(self): pass


class NavigationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'keymap.json'
        self.data = C.defaults()
        self.data['extra_buttons'] = {'home': E.KEY_HOME, 'tv': E.KEY_GRAVE}
        self.data['profiles']['codex']['home'] = 'Super'
        self.data['profiles']['wechat'].update(home='Super', tv='Alt+A')
        self.data['profiles']['other']['home'] = 'Super'
        self.data['navigation'] = {'enabled': True, 'pointer_speed': 3, 'scroll_speed': 3}
        C.save(self.data, None, self.path)
        self.store = C.MappingStore(self.path); self.store.refresh()
        self.time, self.context = 100., 'codex'
        self.mouse, self.keyboard, self.indicator = Output(), Output(), Indicator()
        def factory(mappings, tap, release, **kwargs):
            return MouseNavigation(mappings, tap, release, mouse_factory=lambda: self.mouse,
                                   indicator=self.indicator, clock=lambda: self.time, log=lambda _: None)
        self.router = MappingKeyboard(self.keyboard, lambda: self.context, self.store,
                                      monitor=False, navigation_factory=factory)
        self.nav = self.router.navigation
        self.addCleanup(self.router.close)

    def raw(self, code, value):
        e = event(code, value)
        taken = self.router.intercept(e)
        if not taken: self.router.write_event(e)
        return taken

    def advance(self, seconds):
        self.time += seconds
        self.router.check_focus()
        self.nav.tick(self.context)

    def enter(self):
        self.raw(E.KEY_HOME, 1); self.advance(.61); self.raw(E.KEY_HOME, 0)
        self.assertTrue(self.nav.active)
        self.assertEqual(self.keyboard.events, [])

    def test_short_home_preserves_configured_super_on_release(self):
        self.raw(E.KEY_HOME, 1)
        self.assertEqual(self.keyboard.events, [])
        self.advance(.1); self.raw(E.KEY_HOME, 0)
        self.assertEqual(self.keyboard.events, [(E.EV_KEY, E.KEY_LEFTMETA, 1), (E.EV_KEY, E.KEY_LEFTMETA, 0)])
        self.assertFalse(self.nav.active)

    def test_long_home_without_repeat_toggles_once_and_emits_no_super(self):
        self.raw(E.KEY_HOME, 1); self.advance(.61)
        self.raw(E.KEY_HOME, 2); self.advance(.5); self.raw(E.KEY_HOME, 0)
        self.assertTrue(self.nav.active)
        self.raw(E.KEY_HOME, 1); self.advance(.61); self.raw(E.KEY_HOME, 0)
        self.assertFalse(self.nav.active)
        self.assertEqual(self.keyboard.events, [])

    def test_delayed_release_still_recognizes_long_press(self):
        self.raw(E.KEY_HOME, 1); self.time += .7; self.raw(E.KEY_HOME, 0)
        self.assertTrue(self.nav.active)
        self.assertEqual(self.keyboard.events, [])

    def test_movement_continues_without_hardware_repeat_and_stops_on_release(self):
        self.enter(); self.raw(E.KEY_RIGHT, 1)
        for _ in range(30): self.advance(.02)
        self.assertGreater(sum(v for k,c,v in self.mouse.events if c == E.REL_X and k == E.EV_REL), 20)
        self.assertEqual(self.keyboard.events, [])
        self.raw(E.KEY_RIGHT, 0); before = list(self.mouse.events)
        self.advance(.2)
        self.assertEqual(before, self.mouse.events)

    def test_short_confirm_left_click_and_long_confirm_only_right_click(self):
        self.enter(); self.raw(E.KEY_ENTER, 1); self.advance(.1); self.raw(E.KEY_ENTER, 0)
        self.assertEqual(self.mouse.events, [(E.EV_KEY, E.BTN_LEFT, 1), (E.EV_KEY, E.BTN_LEFT, 0)])
        self.mouse.events.clear()
        self.raw(E.KEY_ENTER, 1); self.advance(.61); self.raw(E.KEY_ENTER, 2); self.raw(E.KEY_ENTER, 0)
        self.assertEqual(self.mouse.events, [(E.EV_KEY, E.BTN_RIGHT, 1), (E.EV_KEY, E.BTN_RIGHT, 0)])
        self.assertEqual(self.keyboard.events, [])

    def test_scroll_tap_repeat_and_release_never_emit_volume_keys(self):
        self.enter(); self.raw(E.KEY_VOLUMEUP, 1)
        self.advance(.5); self.advance(.3); self.raw(E.KEY_VOLUMEUP, 0)
        self.assertEqual(len(self.mouse.events), 3)
        self.assertTrue(all(e == (E.EV_REL, E.REL_WHEEL, 1) for e in self.mouse.events))
        before = list(self.mouse.events); self.advance(.5)
        self.assertEqual(before, self.mouse.events); self.assertEqual(self.keyboard.events, [])

    def test_back_esc_and_voice_prompt_are_not_backspace_or_f5(self):
        self.enter(); self.raw(E.KEY_BACK, 1); self.raw(E.KEY_BACK, 0)
        self.raw(E.KEY_F5, 1); self.raw(E.KEY_F5, 0)
        self.assertEqual(self.keyboard.events, [(E.EV_KEY, E.KEY_ESC, 1), (E.EV_KEY, E.KEY_ESC, 0)])
        self.assertIn('输入框', self.indicator.states[-1][2])

    def test_focus_change_cancels_pending_click_and_motion(self):
        self.enter(); self.raw(E.KEY_ENTER, 1); self.context = 'wechat'; self.advance(.2)
        self.raw(E.KEY_ENTER, 0)
        self.assertTrue(self.nav.active)
        self.assertEqual(self.nav.context, 'wechat')
        self.assertEqual(self.mouse.events, []); self.assertEqual(self.keyboard.events, [])
        self.raw(E.KEY_RIGHT, 1); self.advance(.3)
        self.context = 'other'; self.advance(.2)
        before = list(self.mouse.events)
        self.advance(.4); self.raw(E.KEY_RIGHT, 2); self.raw(E.KEY_RIGHT, 0)
        self.assertEqual(before, self.mouse.events)
        self.assertTrue(self.nav.active)
        self.raw(E.KEY_ENTER, 1); self.raw(E.KEY_ENTER, 0)
        self.assertEqual(self.mouse.events[-2:], [(E.EV_KEY,E.BTN_LEFT,1),(E.EV_KEY,E.BTN_LEFT,0)])

    def test_config_change_cancels_pending_home_without_super(self):
        self.raw(E.KEY_HOME, 1)
        self.data['navigation']['pointer_speed'] = 4
        C.save(self.data, C.read_bytes(self.path), self.path)
        self.advance(.1); self.raw(E.KEY_HOME, 0)
        self.assertFalse(self.nav.active); self.assertEqual(self.keyboard.events, [])

    def test_detach_cancels_and_clears_all_physical_state(self):
        self.enter(); self.raw(E.KEY_RIGHT, 1); before = list(self.mouse.events)
        self.router.detach(); self.advance(.3)
        self.assertEqual(before, self.mouse.events)
        self.assertFalse(self.nav.active); self.assertEqual(self.nav.owned, {}); self.assertEqual(self.nav.physical, set())

    def test_lost_keyup_stops_without_click_but_ioctl_ahead_of_queue_does_not_eat_tap(self):
        self.raw(E.KEY_HOME, 1); self.time += .1; self.nav.reconcile([]); self.raw(E.KEY_HOME, 0)
        self.assertEqual(len(self.keyboard.events), 2)
        self.keyboard.events.clear(); self.enter(); self.raw(E.KEY_ENTER, 1)
        self.nav.reconcile([]); self.time += .17; self.nav.reconcile([])
        self.raw(E.KEY_ENTER, 0)
        self.assertEqual(self.mouse.events, [])

    def test_missing_physical_device_fails_closed(self):
        self.enter(); self.raw(E.KEY_ENTER, 1); self.nav.reconcile(None); self.raw(E.KEY_ENTER, 0)
        self.assertFalse(self.nav.active); self.assertEqual(self.mouse.events, [])

    def test_mode_change_is_rejected_while_voice_is_held(self):
        # 此测试不向上游发送语音，只测试状态机对物理按键的记忆。
        self.router.intercept(event(E.KEY_F5, 1))
        self.raw(E.KEY_HOME, 1); self.advance(.61); self.raw(E.KEY_HOME, 0)
        self.assertFalse(self.nav.active); self.assertEqual(self.keyboard.events, [])

    def test_other_and_claude_can_enter_exit_and_keep_short_super(self):
        for context in ('other', 'claude'):
            with self.subTest(context=context):
                self.context = context
                self.enter()
                self.raw(E.KEY_HOME, 1); self.advance(.61); self.raw(E.KEY_HOME, 0)
                self.assertFalse(self.nav.active)
                self.raw(E.KEY_HOME, 1); self.advance(.1); self.raw(E.KEY_HOME, 0)
                self.assertEqual(self.keyboard.events, [(E.EV_KEY,E.KEY_LEFTMETA,v) for v in (1,0)])
                self.keyboard.events.clear()

    def test_disabled_global_navigation_preserves_original_home(self):
        self.data['navigation']['enabled'] = False
        C.save(self.data, C.read_bytes(self.path), self.path)
        self.context = 'other'
        self.assertFalse(self.raw(E.KEY_HOME, 1)); self.advance(.7); self.raw(E.KEY_HOME, 0)
        self.assertFalse(self.nav.active)
        self.assertEqual(self.keyboard.events, [(E.EV_KEY,E.KEY_LEFTMETA,v) for v in (1,0)])

    def test_claude_mouse_backspace_repeat_and_focus_change_release(self):
        self.context = 'claude'; self.enter()
        self.raw(E.KEY_BACK, 1); self.raw(E.KEY_BACK, 2)
        self.context = 'other'; self.advance(.1)
        self.raw(E.KEY_BACK, 2); self.raw(E.KEY_BACK, 0)
        self.assertEqual(self.keyboard.events, [(E.EV_KEY,E.KEY_BACKSPACE,v) for v in (1,2,0)])
        self.assertTrue(self.nav.active)
        self.keyboard.events.clear()
        self.raw(E.KEY_BACK, 1); self.raw(E.KEY_BACK, 0)
        self.assertEqual(self.keyboard.events, [(E.EV_KEY,E.KEY_ESC,v) for v in (1,0)])

    def test_wechat_screenshot_preserved_inside_mouse_mode(self):
        self.context = 'wechat'; self.enter(); self.raw(E.KEY_GRAVE, 1); self.raw(E.KEY_GRAVE, 0)
        self.assertEqual(self.keyboard.events, [(E.EV_KEY,c,v) for c,v in
            [(E.KEY_LEFTALT,1),(E.KEY_A,1),(E.KEY_A,0),(E.KEY_LEFTALT,0)]])
        self.assertTrue(self.nav.active)

    def test_wechat_mouse_mode_preserves_backspace_repeat_and_release(self):
        self.context = 'wechat'; self.enter()
        for value in (1, 2, 2, 0): self.raw(E.KEY_BACK, value)
        self.assertEqual(self.keyboard.events, [(E.EV_KEY,E.KEY_BACKSPACE,v) for v in (1,2,2,0)])
        self.assertTrue(self.nav.active)

    def test_wechat_custom_back_action_and_focus_change_release_work_in_mouse_mode(self):
        self.data['profiles']['wechat']['back'] = 'Delete'
        C.save(self.data, C.read_bytes(self.path), self.path)
        self.context = 'wechat'; self.enter()
        self.raw(E.KEY_BACK, 1); self.context = 'codex'; self.advance(.1)
        self.raw(E.KEY_BACK, 2); self.raw(E.KEY_BACK, 0)
        self.assertEqual(self.keyboard.events, [(E.EV_KEY,E.KEY_DELETE,v) for v in (1,0)])
        self.assertTrue(self.nav.active)

    def test_upstream_voice_aliases_are_swallowed_in_mouse_mode(self):
        self.enter()
        for context in ('codex','wechat','claude','other'):
            self.context = context; self.advance(.1)
            for code in (E.KEY_F5,173,191):
                self.assertTrue(self.raw(code,1));self.assertTrue(self.raw(code,0))
        self.assertEqual(self.keyboard.events, [])

    def test_no_indicator_means_no_mouse_mode(self):
        self.indicator.available = False
        self.raw(E.KEY_HOME, 1); self.advance(.61); self.raw(E.KEY_HOME, 0)
        self.assertFalse(self.nav.active); self.assertEqual(self.keyboard.events, [])

    def test_output_failure_is_consumed_not_converted_to_enter(self):
        self.enter(); self.raw(E.KEY_ENTER, 1)
        with patch.object(self.mouse, 'write', side_effect=OSError('disconnected')):
            self.assertTrue(self.raw(E.KEY_ENTER, 0))
        self.assertFalse(self.nav.active); self.assertEqual(self.keyboard.events, [])

    def test_max_hold_stops_movement(self):
        self.enter(); self.raw(E.KEY_RIGHT, 1)
        self.advance(10); before = list(self.mouse.events); self.advance(1)
        self.assertEqual(before, self.mouse.events)

    def test_detection_cancels_mode_and_does_not_leave_home_suppressed(self):
        self.enter()
        class Device:
            def read_loop(_self):
                yield event(E.KEY_HOME, 1)
                yield event(E.KEY_HOME, 0)
        with patch('key_detection.detecting', return_value=True), patch('key_detection.write_state'):
            self.assertEqual(list(EventDevice(Device(), self.router, self.store, lambda: self.context).read_loop()), [])
        self.assertFalse(self.nav.active)
        self.enter()


class NavigationConfigTests(unittest.TestCase):
    def test_legacy_config_default_is_disabled(self):
        self.assertFalse(C.navigation_options(C.decode(None))['enabled'])

    def test_strict_config_and_learned_home_requirement(self):
        for options in [{'enabled': 1}, {'pointer_speed': True}, {'scroll_speed': 0}, {'pointer_speed': 6}, {'extra': 1}]:
            data = C.defaults(); data['navigation'] = options
            with self.subTest(options=options), self.assertRaises(ValueError): C.validate(data)
        data = C.defaults(); data['navigation'] = {'enabled': True}
        self.assertEqual(C.pending_bindings(data), ['home'])


if __name__ == '__main__': unittest.main()
