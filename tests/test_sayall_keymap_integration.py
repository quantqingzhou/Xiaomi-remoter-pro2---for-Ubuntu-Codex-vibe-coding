# SPDX-License-Identifier: GPL-3.0-only
"""使用模拟硬件测试已安装的 SayAll 事件循环，不注入真实按键。"""
import support
import importlib.machinery
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import evdev

E = evdev.ecodes
upstream_path = Path('/usr/bin/sayall-bridge')
wrapper_path = Path(os.environ.get('SAYALL_WRAPPER_PATH', str(Path(__file__).resolve().parents[1] / 'src' / 'sayall-reconnect.py')))


def load_module(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FakeKeyboard:
    def __init__(self, events, **kwargs):
        self.capabilities = events
        self.events = []
    def write(self, kind, code, value):
        self.events.append((kind, code, value))
    def write_event(self, event):
        self.write(event.type, event.code, event.value)
    def syn(self): pass
    def close(self): pass


class FakeDevice:
    info = SimpleNamespace(product=0x32B8, version=1)
    def __init__(self, events): self.events = events
    def capabilities(self, verbose=False):
        return {E.EV_KEY: [E.KEY_F5, E.KEY_BACK, E.KEY_ENTER, E.KEY_UP]}
    def read_loop(self):
        for code, value in self.events:
            yield evdev.InputEvent(0, 0, E.EV_KEY, code, value)
    def ungrab(self): pass
    def close(self): pass


@unittest.skipUnless(upstream_path.exists(), 'SayAll 集成测试需要已安装 /usr/bin/sayall-bridge')
class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.upstream = load_module('installed_sayall_test', upstream_path)
        self.wrapper = load_module('sayall_wrapper_test', wrapper_path)

    def run_events(self, codex, events):
        voices = []
        actions = []
        hid_class = self.wrapper.contextual_remote_hid(self.upstream.RemoteHid, lambda: codex)
        hid = hid_class(lambda: voices.append('down'), lambda: voices.append('up'),
                        log=lambda _: None,
                        get_action=lambda code: {'single': 'command:send'} if code == E.KEY_ENTER else {},
                        perform_action=lambda action, code: actions.append((action, code)))
        device = FakeDevice(events)
        with patch('evdev.UInput', FakeKeyboard):
            hid._ensure_uinput(device)
        hid.dev = device
        hid.running = True
        keyboard = hid.ui.keyboard
        try:
            hid._read_events()
        finally:
            hid.stop()
        return keyboard, voices, actions

    def test_codex_back_mapping_preserves_voice_confirm_and_other_keys(self):
        keyboard, voices, actions = self.run_events(True, [
            (E.KEY_BACK, 1), (E.KEY_BACK, 2), (E.KEY_BACK, 0),
            (E.KEY_F5, 1), (E.KEY_F5, 0),
            (E.KEY_ENTER, 1), (E.KEY_ENTER, 0), (E.KEY_UP, 1), (E.KEY_UP, 0)])
        self.assertEqual(keyboard.events, [(E.EV_KEY, E.KEY_BACKSPACE, v) for v in [1, 2, 0]]
                         + [(E.EV_KEY, E.KEY_UP, v) for v in [1, 0]])
        self.assertIn(E.KEY_BACKSPACE, keyboard.capabilities[E.EV_KEY])
        self.assertEqual(voices, ['down', 'up'])
        self.assertEqual(actions, [('command:send', E.KEY_ENTER)])

    def test_other_application_keeps_original_back_events(self):
        keyboard, _, _ = self.run_events(False, [(E.KEY_BACK, v) for v in [1, 2, 0]])
        self.assertEqual(keyboard.events, [(E.EV_KEY, E.KEY_BACK, v) for v in [1, 2, 0]])

    def test_hid_detach_releases_backspace_without_a_physical_keyup(self):
        keyboard, _, _ = self.run_events(True, [(E.KEY_BACK, 1)])
        self.assertEqual(keyboard.events, [(E.EV_KEY, E.KEY_BACKSPACE, v) for v in [1, 0]])
