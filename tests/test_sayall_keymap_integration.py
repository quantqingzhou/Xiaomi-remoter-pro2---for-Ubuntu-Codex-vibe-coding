# SPDX-License-Identifier: GPL-3.0-only
"""使用模拟硬件测试已安装的 SayAll 事件循环，不注入真实按键。"""
import support
import importlib.machinery
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
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

    def run_events(self, codex, events, mappings=None):
        voices = []
        actions = []
        hid_class = self.wrapper.contextual_remote_hid(self.upstream.RemoteHid, lambda: codex, mappings=mappings)
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

    def test_wechat_back_mapping_preserves_voice_confirm_and_repeat(self):
        keyboard, voices, actions = self.run_events('wechat', [
            (E.KEY_BACK, 1), (E.KEY_BACK, 2), (E.KEY_BACK, 0),
            (E.KEY_F5, 1), (E.KEY_F5, 0),
            (E.KEY_ENTER, 1), (E.KEY_ENTER, 0), (E.KEY_UP, 1), (E.KEY_UP, 0)])
        self.assertEqual(keyboard.events, [(E.EV_KEY, E.KEY_BACKSPACE, v) for v in [1, 2, 0]]
                         + [(E.EV_KEY, E.KEY_UP, v) for v in [1, 0]])
        self.assertEqual(voices, ['down', 'up'])
        self.assertEqual(actions, [('command:send', E.KEY_ENTER)])

    def test_hid_detach_releases_backspace_without_a_physical_keyup(self):
        keyboard, _, _ = self.run_events(True, [(E.KEY_BACK, 1)])
        self.assertEqual(keyboard.events, [(E.EV_KEY, E.KEY_BACKSPACE, v) for v in [1, 0]])

    def test_custom_router_with_real_upstream_keeps_voice_and_confirm_path(self):
        from keymap_config import MappingStore, defaults, save
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'keymap.json'
            data = defaults()
            data['profiles']['codex'].update(up='Ctrl+Z', power='Escape')
            save(data, None, path)
            with patch('key_detection.detecting', return_value=False):
                keyboard, voices, actions = self.run_events('codex', [
                    (E.KEY_UP, 1), (E.KEY_UP, 2), (E.KEY_UP, 0),
                    (E.KEY_POWER, 1), (E.KEY_POWER, 0),
                    (E.KEY_BACK, 1), (E.KEY_BACK, 0),
                    (E.KEY_F5, 1), (E.KEY_F5, 0),
                    (E.KEY_ENTER, 1), (E.KEY_ENTER, 0)], MappingStore(path))
            self.assertEqual(keyboard.events, [(E.EV_KEY, c, v) for c, v in [
                (E.KEY_LEFTCTRL, 1), (E.KEY_Z, 1), (E.KEY_Z, 0), (E.KEY_LEFTCTRL, 0),
                (E.KEY_ESC, 1), (E.KEY_ESC, 0), (E.KEY_BACKSPACE, 1), (E.KEY_BACKSPACE, 0)]])
            self.assertEqual(voices, ['down', 'up'])
            self.assertEqual(actions, [('command:send', E.KEY_ENTER)])

    def test_mouse_mode_intercepts_real_upstream_send_voice_and_preserves_wechat_tv(self):
        from keymap_config import MappingStore, defaults, save
        from test_mouse_navigation import Indicator
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'keys.json'
            data = defaults()
            data['extra_buttons'] = {'home': E.KEY_HOME, 'tv': E.KEY_GRAVE}
            data['profiles']['wechat'] = {'home': 'Super', 'tv': 'Alt+A'}
            data['navigation'] = {'enabled': True}
            save(data, None, path)
            voices, actions = [], []
            hid_class = self.wrapper.contextual_remote_hid(self.upstream.RemoteHid,
                            lambda: 'wechat', mappings=MappingStore(path))
            hid = hid_class(lambda: voices.append('down'), lambda: voices.append('up'),
                            log=lambda _: None,
                            get_action=lambda code: {'single': 'command:send'} if code == E.KEY_ENTER else {},
                            perform_action=lambda action, code: actions.append((action, code)))
            class TimedDevice(FakeDevice):
                def read_loop(device):
                    for code, value, age in device.events:
                        tick[0] += age
                        hid.ui.navigation.tick('wechat')
                        yield evdev.InputEvent(0, 0, E.EV_KEY, code, value)
            # 在鼠标模式按确认和语音，随后退回普通模式再各按一次。
            events = [(E.KEY_HOME,1,0), (E.KEY_HOME,0,.7),
                      (E.KEY_ENTER,1,.01), (E.KEY_ENTER,0,.1),
                      (E.KEY_F5,1,.01), (E.KEY_F5,0,.1),
                      (E.KEY_GRAVE,1,.01), (E.KEY_GRAVE,0,.1),
                      (E.KEY_HOME,1,.01), (E.KEY_HOME,0,.7),
                      (E.KEY_ENTER,1,.01), (E.KEY_ENTER,0,.1),
                      (E.KEY_F5,1,.01), (E.KEY_F5,0,.1)]
            device = TimedDevice(events); tick = [100.]
            # 隔离监控线程，避免用真实时间/设备干扰确定性事件序列。
            from mapping_keyboard import MappingKeyboard
            original_init = MappingKeyboard.__init__
            def init(instance, *args, **kwargs):
                original_init(instance, *args, **kwargs, monitor=False)
            with patch('evdev.UInput', FakeKeyboard), patch.object(MappingKeyboard, '__init__', init), \
                 patch('mouse_indicator.MouseIndicator', lambda **kw: Indicator()), \
                 patch('key_detection.detecting', return_value=False):
                hid._ensure_uinput(device)
                hid.ui.navigation.clock = lambda: tick[0]
                hid.ui.navigation.last_tick = tick[0]
                keyboard = hid.ui.keyboard
                hid.dev, hid.running = device, True
                try:
                    hid._read_events()
                    mouse = hid.ui.navigation.mouse
                finally: hid.stop()
            self.assertEqual(actions, [('command:send', E.KEY_ENTER)])
            self.assertEqual(voices, ['down', 'up'])
            self.assertEqual(mouse.events, [(E.EV_KEY,E.BTN_LEFT,1), (E.EV_KEY,E.BTN_LEFT,0)])
            self.assertEqual(keyboard.events, [(E.EV_KEY,c,v) for c,v in
                            [(E.KEY_LEFTALT,1),(E.KEY_A,1),(E.KEY_A,0),(E.KEY_LEFTALT,0)]])
