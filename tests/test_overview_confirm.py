# SPDX-License-Identifier: GPL-3.0-only
"""总览确认只在 GNOME 明确报告打开时放行；不向真实桌面发送输入。"""
import support
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from evdev import ecodes as E
from keymap_config import MappingStore, save, defaults

path = Path(os.environ.get('KEY_RELAY_PATH', str(Path(__file__).resolve().parents[1] / 'src/codex-ptt-key.py')))
spec = importlib.util.spec_from_file_location('overview_key_relay', path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Keyboard:
    def __init__(self): self.events = []
    def write(self, kind, code, value): self.events.append((code, value))
    def syn(self): pass


class OverviewConfirmTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'keymap.json'
        data = defaults(); data['profiles']['wechat']['confirm'] = 'Ctrl+Enter'
        save(data, None, path)
        self.keyboard = Keyboard()
        self.context, self.overview, self.mouse = None, True, False
        self.relay = m.KeyRelay(self.keyboard, lambda: self.context, lambda: False,
            mappings=MappingStore(path), overview_active=lambda: self.overview,
            navigation_active=lambda: self.mouse)

    def test_overview_uses_enter_for_unknown_and_stale_app_focus(self):
        for context in (None, 'codex', 'wechat', 'claude'):
            with self.subTest(context=context):
                self.context = context
                self.keyboard.events.clear()
                self.assertIn('活动概览', self.relay.apply('send', 1))
                self.assertEqual(self.keyboard.events, [(E.KEY_ENTER,1),(E.KEY_ENTER,0)])

    def test_overview_closed_restores_other_disabled_and_custom_wechat(self):
        self.overview = False
        self.relay.apply('send', 1)
        self.assertEqual(self.keyboard.events, [])
        self.context = 'wechat'; self.relay.apply('send', 2)
        self.assertEqual(self.keyboard.events, [(E.KEY_LEFTCTRL,1),(E.KEY_ENTER,1),
                                                (E.KEY_ENTER,0),(E.KEY_LEFTCTRL,0)])

    def test_no_cached_permission_after_overview_closes(self):
        self.relay.apply('send', 1)
        self.overview = False; self.relay.apply('send', 2)
        self.assertEqual(self.keyboard.events, [(E.KEY_ENTER,1),(E.KEY_ENTER,0)])

    def test_mouse_mode_and_voice_release_guard_take_priority(self):
        self.mouse = True; self.relay.apply('send', 1)
        self.mouse = False; self.relay.wait_for_release = True; self.relay.apply('send', 2)
        self.relay.wait_for_release = False; self.relay.held = True; self.relay.apply('send', 3)
        self.assertEqual(self.keyboard.events, [])

    def test_overview_cannot_start_dictation_using_stale_app_focus(self):
        self.context = 'claude'; self.relay.apply('start', 1)
        self.assertTrue(self.relay.wait_for_release)
        self.assertEqual(self.keyboard.events, [])

    def test_read_only_property_requires_exact_true(self):
        for value, expected in [('(<true>,)\n',True),('(<false>,)',False),('',False),('true',False)]:
            with self.subTest(value=value), patch.object(m.subprocess,'check_output',return_value=value) as call:
                self.assertEqual(m.gnome_overview_active(),expected)
                self.assertIn('org.freedesktop.DBus.Properties.Get',call.call_args.args[0])
                self.assertLessEqual(call.call_args.kwargs['timeout'],.3)

    def test_missing_command_bus_failure_and_timeout_do_not_enable_enter(self):
        for exc in (FileNotFoundError(),subprocess.CalledProcessError(1,'gdbus'),subprocess.TimeoutExpired('gdbus',.3)):
            with self.subTest(exc=type(exc)), patch.object(m.subprocess,'check_output',side_effect=exc):
                self.assertFalse(m.gnome_overview_active())


if __name__ == '__main__': unittest.main()
