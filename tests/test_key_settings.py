# SPDX-License-Identifier: GPL-3.0-only
"""配置、热读取与事件生命周期测试；不接触真实遥控器或音频。"""
import support
import importlib.util
import json
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from evdev import ecodes as E
import keymap_config as C
import key_detection as D
from mapping_keyboard import MappingKeyboard, MappingCapabilities
from key_settings import mapping_status, recorded_shortcut, app_version


class Keyboard:
    def __init__(self): self.events = []
    def write(self, kind, code, value): self.events.append((code, value))
    def write_event(self, event): self.write(event.type, event.code, event.value)
    def syn(self): pass
    def close(self): pass


def event(code, value):
    return SimpleNamespace(type=E.EV_KEY, code=code, value=value)


class ConfigTests(unittest.TestCase):
    def test_repair_saves_exact_invalid_bytes_and_keeps_valid_previous_backup(self):
        previous = self.path.with_suffix('.previous.json')
        previous.write_bytes(b'valid previous backup')
        damaged = b'{broken'
        self.path.write_bytes(damaged)
        raw, backup = C.recover_defaults(damaged, self.path)
        self.assertEqual(C.decode(raw), C.defaults())
        self.assertEqual(backup.read_bytes(), damaged)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(previous.read_bytes(), b'valid previous backup')
        with self.assertRaisesRegex(ValueError, '有效'): C.recover_defaults(raw, self.path)

    def test_repair_refuses_concurrent_changes_and_preserves_file_on_write_failure(self):
        self.path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '另一处'): C.recover_defaults(b'old', self.path)
        with patch.object(C, 'atomic_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): C.recover_defaults(b'changed', self.path)
        self.assertEqual(self.path.read_bytes(), b'changed')
        self.assertEqual(next(self.path.parent.glob('keymap.invalid-*')).read_bytes(), b'changed')

    def test_parser_recursion_error_keeps_last_valid_mapping(self):
        store = C.MappingStore(self.path, log=lambda _: None)
        C.save(C.defaults(), None, self.path); store.refresh()
        before = store.revision
        self.path.write_bytes(b'[]')
        with patch.object(C.json, 'loads', side_effect=RecursionError): store.refresh()
        self.assertEqual(store.revision, before)
        self.assertIn('无法解析', store.error)

    def test_single_super_is_allowed_without_allowing_other_bare_modifiers(self):
        self.assertEqual(C.shortcut_codes('Super'), (E.KEY_LEFTMETA,))
        for value in ('Ctrl', 'Alt', 'Shift', 'Ctrl+Super', 'Super+Super'):
            with self.subTest(value=value), self.assertRaises(ValueError): C.shortcut_codes(value)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'keymap.json'

    def test_save_backup_undo_and_conflicting_writer(self):
        data = C.defaults()
        data['profiles']['codex']['back'] = 'Ctrl+Z'
        raw = C.save(data, None, self.path)
        self.assertEqual(C.decode(C.read_bytes(self.path.with_suffix('.previous.json'))), C.defaults())
        with self.assertRaisesRegex(ValueError, '另一处'):
            C.save(C.defaults(), None, self.path)
        C.save(C.defaults(), raw, self.path)
        self.assertEqual(C.decode(C.read_bytes(self.path.with_suffix('.previous.json'))), data)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_invalid_file_preserves_last_good_mapping(self):
        data = C.defaults()
        data['profiles']['codex']['back'] = 'Ctrl+Z'
        C.save(data, None, self.path)
        store = C.MappingStore(self.path, log=lambda _: None)
        store.refresh()
        self.path.write_text('{broken')
        revision = store.revision
        store.refresh()
        self.assertEqual(store.revision, revision)
        self.assertEqual(store.codes('back', 'codex'), (E.KEY_LEFTCTRL, E.KEY_Z))
        self.assertIsNotNone(store.error)

    def test_reserved_voice_shortcut_and_shell_text_rejected(self):
        for text in ['F5', 'RightAlt', 'Ctrl+F5', 'Ctrl+Ctrl+Z', 'command:rm', '$(touch /tmp/foo)', 'Ctrl', '']:
            with self.subTest(text=text), self.assertRaises(ValueError): C.shortcut_codes(text)

    def test_invalid_bindings_and_voice_override_rejected(self):
        for extra in [{'home': E.KEY_F5}, {'home': E.KEY_ENTER}, {'home': True}, {'home': 102, 'menu': 102}]:
            data = C.defaults(); data['extra_buttons'] = extra
            with self.assertRaises(ValueError): C.validate(data)
        data = C.defaults(); data['profiles']['codex']['voice'] = 'Enter'
        with self.assertRaises(ValueError): C.validate(data)

    def test_unlearned_tv_action_cannot_be_saved_as_ready(self):
        data = C.defaults()
        data['profiles']['wechat']['tv'] = 'Alt+A'
        self.assertEqual(C.pending_bindings(data), ['tv'])
        with self.assertRaisesRegex(ValueError, '先识别.*电视'):
            C.save(data, None, self.path)
        self.assertFalse(self.path.exists())
        # 旧版已经写出的未绑定配置可以读取，不使后台崩溃。
        self.assertEqual(C.decode(json.dumps(data).encode()), data)

    def test_learning_tv_preserves_wechat_shortcut_and_allows_save(self):
        data = C.defaults(); data['profiles']['wechat']['tv'] = 'Alt+A'
        result = C.bind_button(data, 'tv', E.KEY_GRAVE)
        self.assertEqual(data['extra_buttons'], {})
        self.assertEqual(C.pending_bindings(result), [])
        C.save(result, None, self.path)
        store = C.MappingStore(self.path); store.refresh()
        self.assertEqual(store.button(E.KEY_GRAVE), 'tv')
        self.assertEqual(store.codes('tv', 'wechat'), (E.KEY_LEFTALT, E.KEY_A))

    def test_learning_does_not_steal_known_or_already_bound_keys(self):
        data = C.defaults()
        with self.assertRaises(ValueError): C.bind_button(data, 'tv', E.KEY_ENTER)
        data = C.bind_button(data, 'home', E.KEY_HOME)
        with self.assertRaises(ValueError): C.bind_button(data, 'tv', E.KEY_HOME)
        with self.assertRaises(ValueError): C.bind_button(data, 'voice', E.KEY_GRAVE)

    def test_symlink_refused_and_missing_file_uses_defaults(self):
        store = C.MappingStore(self.path)
        store.refresh()
        self.assertEqual(store.codes('back', 'codex'), (E.KEY_BACKSPACE,))
        self.assertEqual(store.codes('confirm', None), ())
        target = self.path.with_name('target'); target.write_text('{}')
        self.path.symlink_to(target)
        with self.assertRaises(ValueError): C.save(C.defaults(), None, self.path)
        self.assertEqual(target.read_text(), '{}')


class RouterTests(unittest.TestCase):
    def test_unmapped_key_is_released_on_detach_and_shared_output_is_preserved(self):
        self.mapping('up', 'Home')
        self.press(E.KEY_HOME, 1)
        self.press(E.KEY_UP, 1, 0)
        self.assertEqual(self.keyboard.events, [(E.KEY_HOME, 1)])
        self.router.release()
        self.assertEqual(self.keyboard.events, [(E.KEY_HOME, 1), (E.KEY_HOME, 0)])

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'keys.json'
        self.store = C.MappingStore(self.path)
        self.keyboard = Keyboard()
        self.focus = 'codex'
        self.router = MappingKeyboard(self.keyboard, lambda: self.focus, self.store, monitor=False)
        self.addCleanup(self.router.close)

    def mapping(self, button, action, context='codex'):
        data = C.decode(C.read_bytes(self.path)); data['profiles'][context][button] = action
        C.save(data, C.read_bytes(self.path), self.path)

    def press(self, code, *values):
        for value in values: self.router.write_event(event(code, value))

    def test_defaults_preserve_repeat_and_release(self):
        self.press(E.KEY_BACK, 1, 2, 0)
        self.assertEqual(self.keyboard.events, [(E.KEY_BACKSPACE, v) for v in [1, 2, 0]])
        self.focus = None
        self.press(E.KEY_BACK, 1, 0)
        self.assertEqual(self.keyboard.events[-2:], [(E.KEY_BACK, 1), (E.KEY_BACK, 0)])

    def test_chord_once_despite_repeat_and_reverse_release_order(self):
        self.mapping('back', 'Ctrl+Shift+Z')
        self.press(E.KEY_BACK, 1, 1, 2, 2, 0)
        self.assertEqual(self.keyboard.events, [(E.KEY_LEFTCTRL, 1), (E.KEY_LEFTSHIFT, 1),
                        (E.KEY_Z, 1), (E.KEY_Z, 0), (E.KEY_LEFTSHIFT, 0), (E.KEY_LEFTCTRL, 0)])

    def test_enter_never_repeats_when_mapped_to_direction(self):
        self.mapping('up', 'Enter')
        self.press(E.KEY_UP, 1, 2, 2, 0)
        self.assertEqual(self.keyboard.events, [(E.KEY_ENTER, 1), (E.KEY_ENTER, 0)])

    def test_home_super_is_one_tap_and_never_leaves_modifier_held(self):
        data = C.bind_button(C.defaults(), 'home', E.KEY_HOMEPAGE)
        data['profiles']['codex']['home'] = 'Super'
        C.save(data, None, self.path)
        self.press(E.KEY_HOMEPAGE, 1, 2, 2, 0)
        self.assertEqual(self.keyboard.events, [(E.KEY_LEFTMETA, 1), (E.KEY_LEFTMETA, 0)])
        self.assertEqual(self.router.refs, {})

    def test_learned_tv_emits_alt_a_only_in_wechat(self):
        data = C.defaults(); data['profiles']['wechat']['tv'] = 'Alt+A'
        data = C.bind_button(data, 'tv', E.KEY_GRAVE)
        C.save(data, None, self.path)
        self.focus = 'wechat'
        self.press(E.KEY_GRAVE, 1, 2, 0)
        self.assertEqual(self.keyboard.events, [(E.KEY_LEFTALT, 1), (E.KEY_A, 1),
                                              (E.KEY_A, 0), (E.KEY_LEFTALT, 0)])
        self.keyboard.events.clear(); self.focus = 'codex'
        self.press(E.KEY_GRAVE, 1, 0)
        self.assertEqual(self.keyboard.events, [(E.KEY_GRAVE, 1), (E.KEY_GRAVE, 0)])

    def test_focus_change_suppresses_until_physical_release(self):
        self.press(E.KEY_BACK, 1)
        self.focus = 'wechat'; self.router.check_focus()
        self.press(E.KEY_BACK, 2, 1, 0)
        self.assertEqual(self.keyboard.events, [(E.KEY_BACKSPACE, 1), (E.KEY_BACKSPACE, 0)])

    def test_configuration_change_releases_old_key_and_waits_for_next_press(self):
        self.press(E.KEY_BACK, 1)
        self.mapping('back', 'Delete')
        self.router.check_focus(); self.press(E.KEY_BACK, 2, 0, 1, 0)
        self.assertEqual(self.keyboard.events, [(E.KEY_BACKSPACE, 1), (E.KEY_BACKSPACE, 0),
                                               (E.KEY_DELETE, 1), (E.KEY_DELETE, 0)])

    def test_two_buttons_sharing_output_do_not_release_each_other(self):
        self.mapping('up', 'Backspace')
        self.press(E.KEY_BACK, 1); self.press(E.KEY_UP, 1)
        self.press(E.KEY_BACK, 0)
        self.assertEqual(self.keyboard.events, [(E.KEY_BACKSPACE, 1)])
        self.press(E.KEY_UP, 0)
        self.assertEqual(self.keyboard.events[-1], (E.KEY_BACKSPACE, 0))

    def test_disabled_and_disconnect_do_not_leave_pressed_key(self):
        self.mapping('up', 'disabled'); self.press(E.KEY_UP, 1, 2, 0)
        self.assertEqual(self.keyboard.events, [])
        self.press(E.KEY_BACK, 1); self.router.release()
        self.assertEqual(self.keyboard.events[-1], (E.KEY_BACKSPACE, 0))
        self.assertEqual(self.router.refs, {})

    def test_changing_learned_code_while_held_does_not_forward_orphan_release(self):
        data = C.defaults(); data['extra_buttons']['home'] = E.KEY_HOME
        data['profiles']['codex']['home'] = 'Backspace'
        C.save(data, None, self.path)
        self.press(E.KEY_HOME, 1)
        data['extra_buttons']['home'] = E.KEY_COMPOSE
        C.save(data, C.read_bytes(self.path), self.path)
        self.press(E.KEY_HOME, 2, 0)
        self.assertEqual(self.keyboard.events, [(E.KEY_BACKSPACE, 1), (E.KEY_BACKSPACE, 0)])
        self.assertEqual(self.router.down, {})

    def test_mapped_power_key_has_safe_default(self):
        self.press(E.KEY_POWER, 1, 0); self.assertEqual(self.keyboard.events, [])
        self.mapping('power', 'Escape'); self.press(E.KEY_POWER, 1, 0)
        self.assertEqual(self.keyboard.events, [(E.KEY_ESC, 1), (E.KEY_ESC, 0)])

    def test_capabilities_include_shortcuts_without_modifying_original(self):
        caps = {E.EV_KEY: [E.KEY_UP]}
        device = SimpleNamespace(capabilities=lambda verbose=False: caps)
        result = MappingCapabilities(device).capabilities()
        self.assertIn(E.KEY_LEFTCTRL, result[E.EV_KEY])
        self.assertEqual(caps[E.EV_KEY], [E.KEY_UP])


class DetectionTests(unittest.TestCase):
    def test_voice_confirm_and_unknown_keys_do_not_query_focus(self):
        events = [event(code, v) for code in (E.KEY_F5, E.KEY_ENTER, E.KEY_MENU) for v in (1, 0)]
        focus = unittest.mock.Mock(side_effect=AssertionError('unnecessary focus query'))
        wrapper = D.EventDevice(SimpleNamespace(read_loop=lambda: iter(events)), Keyboard(),
                                C.MappingStore('/nonexistent/keymap.json'), focus)
        with patch.object(D, 'detecting', return_value=False): self.assertEqual(list(wrapper.read_loop()), events)
        focus.assert_not_called()

    def filter(self, events, detecting):
        store = C.MappingStore('/nonexistent/keymap.json')
        device = SimpleNamespace(read_loop=lambda: iter(events))
        keyboard = Keyboard()
        wrapper = D.EventDevice(device, keyboard, store, lambda: 'codex')
        with patch.object(D, 'detecting', side_effect=detecting), patch.object(D, 'write_state'):
            return list(wrapper.read_loop())

    def test_test_mode_swallows_voice_confirm_and_volume(self):
        events = [event(code, v) for code in [E.KEY_F5, E.KEY_ENTER, E.KEY_VOLUMEUP] for v in [1, 0]]
        self.assertEqual(self.filter(events, [True] * len(events)), [])

    def test_capture_expiry_while_held_waits_for_release(self):
        events = [event(E.KEY_F5, v) for v in [1, 2, 0, 1, 0]]
        self.assertEqual(self.filter(events, [True, False, False, False, False]), events[-2:])

    def test_capture_start_during_voice_hold_preserves_stop(self):
        events = [event(E.KEY_F5, v) for v in [1, 0, 1, 0]]
        self.assertEqual(self.filter(events, [False, True, True, True]), events[:2])


class ConfirmTests(unittest.TestCase):
    def test_context_mapping_respects_dictation_lock_and_default_other(self):
        spec = importlib.util.spec_from_file_location('custom_key_relay', Path(__file__).resolve().parents[1] / 'src/codex-ptt-key.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'keymap.json'
            data = C.defaults(); data['profiles']['codex']['confirm'] = 'Ctrl+Enter'
            C.save(data, None, path)
            keyboard = Keyboard()
            focus = ['codex']
            relay = module.KeyRelay(keyboard, lambda: focus[0], lambda: True,
                                    playback_safe=lambda: True, mappings=C.MappingStore(path))
            relay.apply('send', 0)
            self.assertEqual(keyboard.events, [(E.KEY_LEFTCTRL, 1), (E.KEY_ENTER, 1),
                                              (E.KEY_ENTER, 0), (E.KEY_LEFTCTRL, 0)])
            keyboard.events.clear(); focus[0] = None
            relay.apply('send', 0); self.assertEqual(keyboard.events, [])
            relay.held = True; focus[0] = 'codex'
            relay.apply('send', 0); self.assertEqual(keyboard.events, [])


class FeedbackTests(unittest.TestCase):
    def test_version_matches_source_and_installed_layout_and_missing_is_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'src'; source.mkdir()
            self.assertEqual(app_version(source),'版本未知')
            (root / 'VERSION').write_text('0.1.0-alpha.8\n')
            self.assertEqual(app_version(source),'0.1.0-alpha.8')
            self.assertEqual(app_version(root),'0.1.0-alpha.8')
            (root / 'VERSION').write_text('invalid\nversion')
            self.assertEqual(app_version(source),'版本未知')

    def test_saved_bytes_need_fresh_matching_bridge_acknowledgement(self):
        raw = json.dumps(C.defaults()).encode()
        health = {'time': 10, 'sha256': hashlib.sha256(raw).hexdigest()}
        self.assertEqual(mapping_status(raw, health, 11), ('ready', ''))
        self.assertEqual(mapping_status(raw, health, 14)[0], 'offline')
        self.assertEqual(mapping_status(raw, {**health, 'sha256': 'different'}, 11)[0], 'pending')
        self.assertEqual(mapping_status(raw, {**health, 'error': 'parse failure'}, 11)[0], 'error')
        data = C.defaults(); data['profiles']['wechat']['tv'] = 'Alt+A'
        self.assertEqual(mapping_status(json.dumps(data).encode(), health, 11)[0], 'unbound')

    def test_recorded_key_names_support_special_keys_without_allowing_dictation_bypass(self):
        for name, mods, expected in [('Page_Up', [], 'PageUp'), ('Page_Down', [], 'PageDown'),
                ('ISO_Left_Tab', [], 'Shift+Tab'), ('ISO_Left_Tab', ['Ctrl', 'Shift'], 'Ctrl+Shift+Tab'),
                ('KP_Enter', ['Ctrl'], 'Ctrl+Enter'), ('a', ['Alt'], 'Alt+A')]:
            self.assertEqual(recorded_shortcut(name, mods), expected)
        with self.assertRaises(ValueError): recorded_shortcut('F5', [])


if __name__ == '__main__': unittest.main()
