# SPDX-License-Identifier: GPL-3.0-only
"""按下时固定动作；切换应用、改配置或断线时成对释放按键。"""
import threading
import time
from evdev import ecodes as E
from keymap_config import OUTPUT_CODES

REPEATABLE = {E.KEY_BACKSPACE, E.KEY_DELETE, E.KEY_UP, E.KEY_DOWN, E.KEY_LEFT,
              E.KEY_RIGHT, E.KEY_PAGEUP, E.KEY_PAGEDOWN, E.KEY_VOLUMEUP, E.KEY_VOLUMEDOWN}


class MappingCapabilities:
    def __init__(self, device): self.device = device
    def __getattr__(self, name): return getattr(self.device, name)
    def capabilities(self, verbose=False):
        caps = dict(self.device.capabilities(verbose=verbose))
        caps[E.EV_KEY] = sorted(set(caps.get(E.EV_KEY, [])) | set(OUTPUT_CODES))
        return caps


class MappingKeyboard:
    def __init__(self, keyboard, focus, mappings, log=print, monitor=True):
        self.keyboard, self.focus, self.mappings, self.log = keyboard, focus, mappings, log
        self.lock = threading.RLock()
        self.down = {}
        self.refs = {}
        self.revision = mappings.refresh()
        self.stopped = threading.Event()
        self.thread = None
        self.last_report = 0
        if monitor:
            self.thread = threading.Thread(target=self._monitor, daemon=True)
            self.thread.start()

    def __getattr__(self, name): return getattr(self.keyboard, name)

    def context(self):
        value = self.focus()
        return 'codex' if value is True else value if value in ('codex', 'wechat') else 'other'

    def _press(self, codes):
        for code in codes:
            if not self.refs.get(code):
                self.keyboard.write(E.EV_KEY, code, 1)
            self.refs[code] = self.refs.get(code, 0) + 1
        self.keyboard.syn()

    def _release(self, codes):
        for code in reversed(codes):
            self.refs[code] -= 1
            if not self.refs[code]:
                self.keyboard.write(E.EV_KEY, code, 0)
                del self.refs[code]
        self.keyboard.syn()

    def check_focus(self):
        with self.lock:
            revision = self.mappings.refresh()
            context = self.context() if self.down else None
            for state in self.down.values():
                if state['codes'] and (state['context'] != context or revision != self.revision
                                       or time.monotonic() - state['since'] > 90):
                    self._release(state['codes'])
                    state['codes'] = ()
            self.revision = revision

    def write_event(self, event):
        if event.type != E.EV_KEY:
            return self.keyboard.write_event(event)
        with self.lock:
            self.check_focus()
            button = self.mappings.button(event.code)
            if event.code not in self.down and button in {'voice', 'confirm'}:
                return self.keyboard.write_event(event)
            if event.value == 1:
                if event.code in self.down:
                    return
                context = self.context()
                codes = self.mappings.codes(button, context, event.code) if button else (event.code,)
                action = self.mappings.action(button, context) if button else 'default'
                repeat = len(codes) == 1 and (codes[0] in REPEATABLE or action == 'default')
                self._press(codes)
                if codes and not repeat:
                    self._release(codes)
                    codes = ()
                self.down[event.code] = {'codes': codes, 'context': context, 'since': time.monotonic()}
            elif event.value == 0:
                state = self.down.pop(event.code, None)
                if state:
                    self._release(state['codes'])
            elif event.value == 2:
                state = self.down.get(event.code)
                if state and len(state['codes']) == 1:
                    self.keyboard.write(E.EV_KEY, state['codes'][0], 2)
                    self.keyboard.syn()

    def release(self):
        with self.lock:
            for state in self.down.values():
                self._release(state['codes'])
            self.down.clear()

    def _monitor(self):
        while not self.stopped.wait(0.1):
            self.check_focus()
            if time.monotonic() - self.last_report >= 1:
                from key_detection import write_state
                import hashlib
                try:
                    write_state('bridge.json', {'time': time.monotonic(),
                        'sha256': hashlib.sha256(self.mappings.raw or b'').hexdigest(),
                        'error': self.mappings.error})
                except OSError:
                    pass
                self.last_report = time.monotonic()

    def close(self):
        self.stopped.set()
        if self.thread:
            self.thread.join()
        self.release()
        self.keyboard.close()
