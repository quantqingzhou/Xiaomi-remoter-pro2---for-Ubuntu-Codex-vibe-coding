# SPDX-License-Identifier: GPL-3.0-only
"""在 Codex 和已启用的微信窗口中映射退格，保留按下、重复与松开事件。"""
import threading

from evdev import ecodes


class BackspaceCapabilities:
    """增加退格键输出能力，不改变物理设备本身的能力列表。"""
    def __init__(self, device):
        self.device = device

    def __getattr__(self, name):
        return getattr(self.device, name)

    def capabilities(self, verbose=False):
        if verbose:
            raise ValueError('仅支持使用数值表示的按键能力')
        caps = dict(self.device.capabilities(verbose=False))
        keys = list(caps.get(ecodes.EV_KEY, []))
        if ecodes.KEY_BACKSPACE not in keys:
            keys.append(ecodes.KEY_BACKSPACE)
        caps[ecodes.EV_KEY] = keys
        return caps


class ContextualBackKeyboard:
    def __init__(self, keyboard, focus, log=print, monitor=True):
        self.keyboard = keyboard
        self.focus = focus
        self.log = log
        self.lock = threading.RLock()
        self.physical_down = False
        self.output_code = None
        self.context_at_press = None
        self.stopped = threading.Event()
        self.thread = None
        if monitor:
            self.thread = threading.Thread(target=self._monitor, daemon=True)
            self.thread.start()

    def __getattr__(self, name):
        return getattr(self.keyboard, name)

    def _focus_context(self):
        value = self.focus()
        # 兼容旧版只返回 Codex 焦点布尔值的调用方。
        if value is True:
            return 'codex'
        return value if isinstance(value, str) and value in {'codex', 'wechat'} else None

    def _release_output(self):
        if self.output_code is not None:
            self.keyboard.write(ecodes.EV_KEY, self.output_code, 0)
            self.keyboard.syn()
            self.output_code = None

    def check_focus(self):
        with self.lock:
            if self.output_code is not None and self._focus_context() != self.context_at_press:
                self._release_output()
                self.log('焦点已改变，已释放返回键输出；等待物理按键松开')

    def write_event(self, event):
        if event.type != ecodes.EV_KEY or event.code != ecodes.KEY_BACK:
            return self.keyboard.write_event(event)
        with self.lock:
            if event.value == 1:
                if self.physical_down:
                    return
                self.physical_down = True
                self.context_at_press = self._focus_context()
                self.output_code = ecodes.KEY_BACKSPACE if self.context_at_press else ecodes.KEY_BACK
                self.keyboard.write(ecodes.EV_KEY, self.output_code, 1)
                self.keyboard.syn()
                self.log('返回键 → Backspace（Codex／微信）' if self.context_at_press
                         else '返回键 → KEY_BACK（其他应用）')
            elif event.value == 2:
                self.check_focus()
                if self.output_code is not None:
                    self.keyboard.write(ecodes.EV_KEY, self.output_code, 2)
                    self.keyboard.syn()
            elif event.value == 0:
                self._release_output()
                self.physical_down = False
                self.context_at_press = None

    def release(self):
        """设备断开或程序退出时释放按键，即使没有收到物理松开事件。"""
        with self.lock:
            self._release_output()
            self.physical_down = False
            self.context_at_press = None

    def _monitor(self):
        while not self.stopped.wait(0.1):
            self.check_focus()

    def close(self):
        self.stopped.set()
        if self.thread is not None:
            self.thread.join()
        self.release()
        self.keyboard.close()
