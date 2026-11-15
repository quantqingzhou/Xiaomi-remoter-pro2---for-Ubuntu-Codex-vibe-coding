# SPDX-License-Identifier: GPL-3.0-only
"""仅在设置窗口主动检测期间记录遥控器键码；不打开其他输入设备。"""
import json
import os
from pathlib import Path
import time
from evdev import ecodes as E
from keymap_config import atomic_write


def runtime_dir():
    return Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'xiaomi-codex-key-settings'


def read_state(name):
    try:
        value = json.loads((runtime_dir() / name).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(name, value):
    atomic_write(runtime_dir() / name, json.dumps(value).encode())


def detecting():
    value = read_state('detect.json').get('time', 0)
    return type(value) in (int, float) and 0 <= time.monotonic() - value < 1.5


def mouse_mode_active():
    state = read_state('navigation.json')
    stamp = state.get('time', 0)
    return (state.get('active') is True and type(stamp) in (int, float)
            and 0 <= time.monotonic() - stamp < 2)


class EventDevice:
    """检测开始前已按住的键仍交回原流程松开；检测中按下的键整次吞掉。"""
    def __init__(self, device, keyboard, mappings, focus):
        self.device, self.keyboard, self.mappings, self.focus = device, keyboard, mappings, focus
        self.suppressed = set()
        self.forwarded = set()
        self.routed = set()
        if hasattr(keyboard, 'set_physical_reader'):
            keyboard.set_physical_reader(getattr(device, 'active_keys', None))

    def __getattr__(self, name): return getattr(self.device, name)

    def read_loop(self):
        for event in self.device.read_loop():
            if event.type != E.EV_KEY:
                yield event
                continue
            capture = detecting()
            if capture:
                if hasattr(self.keyboard, 'navigation'):
                    with self.keyboard.lock:
                        self.keyboard.navigation.cancel('正在检测实体按键')
                        if event.value == 0:
                            self.keyboard.navigation.owned.pop(event.code, None)
                            self.keyboard.navigation.physical.discard(event.code)
                try:
                    write_state('event.json', {'code': event.code, 'value': event.value,
                                              'time': time.monotonic()})
                except OSError:
                    pass
            if event.code in self.suppressed or (capture and event.code not in self.forwarded):
                if event.value == 1:
                    self.suppressed.add(event.code)
                elif event.value == 0:
                    self.suppressed.discard(event.code)
                continue
            if hasattr(self.keyboard, 'intercept') and self.keyboard.intercept(event):
                continue
            self.mappings.refresh()
            if event.value == 1:
                self.forwarded.add(event.code)
                button = self.mappings.button(event.code)
                # 显式自定义覆盖 SayAll 对该键的旧动作；默认设置继续走上游。
                if button not in (None, 'voice', 'confirm'):
                    context = self.focus()
                    context = 'codex' if context is True else context
                    if self.mappings.action(button, context) != 'default':
                        self.routed.add(event.code)
            routed = event.code in self.routed
            if event.value == 0:
                self.forwarded.discard(event.code)
                self.routed.discard(event.code)
            if routed:
                self.keyboard.write_event(event)
            else:
                yield event
