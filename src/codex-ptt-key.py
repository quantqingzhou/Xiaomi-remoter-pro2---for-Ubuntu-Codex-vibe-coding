#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""持续运行的 uinput 键盘：Codex 使用 F5，可选微信扩展使用右 Alt。"""
from __future__ import annotations
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
import evdev
from remote_config import load_config, normalize_address
from playback_protection import is_protected

RELEASE_GRACE_SECONDS = 1.5
MAX_HOLD_SECONDS = 90
# 此 Python 版本中的 evdev 可能在设备文件描述符失效时抛出 SystemError。
DEVICE_READ_ERRORS = (OSError, SystemError)
RUNTIME = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'xiaomi-codex-remote'
SOCKET = RUNTIME / 'keys.sock'


def focused_window_context():
    """精确匹配已配置的窗口类名；微信扩展默认关闭。"""
    try:
        active = subprocess.check_output(['xprop', '-root', '_NET_ACTIVE_WINDOW'], text=True, timeout=1)
        match = re.search(r'# (0x[0-9a-fA-F]+)', active)
        if not match:
            return None
        wm_class = subprocess.check_output(['xprop', '-id', match.group(1), 'WM_CLASS'], text=True, timeout=1)
        classes = [value.lower() for value in re.findall(r'"([^"\n]*)"', wm_class)]
        config = load_config()
        if any(value in config.codex_wm_classes for value in classes):
            return 'codex'
        if any(value in config.wechat_wm_classes for value in classes):
            return 'wechat'
        return None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def focused_window_is_codex():
    # 返回键仍只在 Codex 中转为退格，微信继续保留原始 KEY_BACK。
    return focused_window_context() == 'codex'


class RemoteState:
    def __init__(self, list_devices=evdev.list_devices, open_device=evdev.InputDevice,
                 *, address, vendor=0x2717, product=0x32B8):
        self.address = normalize_address(address)
        self.vendor, self.product = vendor, product
        self.device = None
        self.list_devices = list_devices
        self.open_device = open_device

    def _find_keyboard(self):
        for path in self.list_devices():
            dev = None
            try:
                dev = self.open_device(path)
                if (dev.uniq.lower() == self.address
                        and dev.info.vendor == self.vendor and dev.info.product == self.product
                        and evdev.ecodes.KEY_F5 in dev.capabilities().get(evdev.ecodes.EV_KEY, [])):
                    self.device = dev
                    return
            except DEVICE_READ_ERRORS:
                # 查找设备期间，其他输入节点可能断开或无法访问。
                pass
            finally:
                if dev is not None and dev is not self.device:
                    try:
                        dev.close()
                    except DEVICE_READ_ERRORS:
                        pass

    def pressed(self):
        """True/False 表示实际读取到的按键状态；None 表示暂时无法读取。"""
        for _ in range(2):
            try:
                if self.device is None:
                    self._find_keyboard()
                if self.device is None:
                    return None
                return evdev.ecodes.KEY_F5 in self.device.active_keys()
            except DEVICE_READ_ERRORS:
                # 立即重新打开设备，让重连后的第一次按键即可生效。
                self.close()
        return None

    def close(self):
        if self.device is not None:
            try:
                self.device.close()
            except DEVICE_READ_ERRORS:
                pass
            finally:
                self.device = None


class KeyRelay:
    def __init__(self, keyboard, focus, remote_pressed, clock=time.monotonic, *, playback_safe=None):
        self.keyboard, self.focus, self.remote_pressed, self.clock = keyboard, focus, remote_pressed, clock
        # 未明确确认保护有效时，不触发可能压低其他软件声音的客户端听写。
        self.playback_safe = playback_safe if playback_safe is not None else lambda: False
        self.held = False
        self.held_code = None
        self.held_context = None
        self.deadline = 0.0
        self.release_pending_since = None

    def key(self, code, down):
        self.keyboard.write(evdev.ecodes.EV_KEY, code, int(down))
        self.keyboard.syn()

    def _focus_context(self):
        value = self.focus()
        # 保持与原先提供布尔值的 Codex 焦点回调兼容。
        if value is True:
            return 'codex'
        return value if isinstance(value, str) and value in {'codex', 'wechat'} else None

    def _held_key_name(self):
        return 'F5' if self.held_code == evdev.ecodes.KEY_F5 else '右 Alt'

    def release(self):
        self.release_pending_since = None
        if self.held:
            name = self._held_key_name()
            self.key(self.held_code, False)
            self.held = False
            self.held_code = None
            self.held_context = None
            return f'{name} 已松开'
        return '听写键已处于松开状态'

    def apply(self, action, stamp):
        # 客户端时间戳反映子进程调度时间，不能代表 HID 事件的先后顺序。
        # 根据物理按键状态判断是否忽略迟到的开始或停止命令。
        if action == 'stop':
            if self.held and self.remote_pressed() is True:
                return '忽略迟到的停止命令：遥控器按键仍按住'
            return self.release()
        if action == 'start':
            if self.held:
                return f'{self._held_key_name()} 已处于按住状态'
            context = self._focus_context()
            if context is None:
                return '跳过听写键：当前窗口未启用遥控器听写'
            # 快速点按可能在开始命令对应的子进程运行前就已结束。
            if self.remote_pressed() is not True:
                return '跳过听写键：遥控器按键已松开或暂时无法读取'
            if context == 'codex' and not self.playback_safe():
                return '跳过 F5：客户端播放保护未生效或已被更新覆盖，请先运行诊断并恢复保护'
            code = evdev.ecodes.KEY_F5 if context == 'codex' else evdev.ecodes.KEY_RIGHTALT
            self.key(code, True)
            self.held_code, self.held_context = code, context
            self.held, self.deadline = True, self.clock() + MAX_HOLD_SECONDS
            self.release_pending_since = None
            return f'{self._held_key_name()} 已按下'
        if action == 'send':
            if self.held:
                return '跳过 Enter：听写键仍按住'
            if self._focus_context() is None:
                return '跳过 Enter：当前窗口未启用遥控器发送'
            self.key(evdev.ecodes.KEY_ENTER, True)
            self.key(evdev.ecodes.KEY_ENTER, False)
            return 'Enter 已发送'
        raise ValueError('未知按键命令')

    def watchdog(self):
        if not self.held:
            self.release_pending_since = None
            return None
        if self.held_context == 'codex' and not self.playback_safe():
            return self.release() + '（客户端播放保护失效，已停止触发听写）'
        now = self.clock()
        if now >= self.deadline:
            return self.release() + '（超时保护触发）'
        if self.remote_pressed() is True:
            self.release_pending_since = None
        elif self.release_pending_since is None:
            self.release_pending_since = now
        elif now - self.release_pending_since >= RELEASE_GRACE_SECONDS:
            return self.release() + '（未收到停止命令，遥控器已松开或暂时不可用）'
        return None


def serve():
    config = load_config()
    RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (RUNTIME / 'keys.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # SayAll 会排除此设备名称；不要将虚拟键盘标识伪装成小米设备。
        with evdev.UInput({evdev.ecodes.EV_KEY: [evdev.ecodes.KEY_F5, evdev.ecodes.KEY_ENTER,
                                               evdev.ecodes.KEY_RIGHTALT]},
                          name='SayAll Codex Keys', vendor=1, product=1, version=1) as keyboard:
            remote = RemoteState(address=config.remote_address, vendor=config.vendor_id,
                                 product=config.product_id)
            relay = KeyRelay(keyboard, focused_window_context, remote.pressed,
                             playback_safe=lambda: is_protected(Path(config.dictation_helper)))
            running = True

            def shutdown(_signum, _frame):
                nonlocal running
                running = False

            signal.signal(signal.SIGTERM, shutdown)
            signal.signal(signal.SIGINT, shutdown)
            SOCKET.unlink(missing_ok=True)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(SOCKET))
                os.chmod(SOCKET, 0o600)
                server.listen(8)
                server.settimeout(0.2)
                print('已就绪：持续运行的 uinput 键盘（F5、右 Alt 和 Enter）', flush=True)
                try:
                    while running:
                        watchdog = relay.watchdog()
                        if watchdog:
                            print(watchdog, flush=True)
                        try:
                            connection, _ = server.accept()
                        except socket.timeout:
                            continue
                        with connection:
                            connection.settimeout(0.5)
                            try:
                                request = json.loads(connection.recv(2048))
                                action = request['action']
                                if action == 'status':
                                    result = {'ready': True, 'voice_key_held': relay.held,
                                              'f5_held': relay.held_code == evdev.ecodes.KEY_F5,
                                              'held_context': relay.held_context,
                                              'wechat_enabled': bool(config.wechat_wm_classes),
                                              'playback_protected': relay.playback_safe()}
                                else:
                                    result = relay.apply(action, int(request['stamp']))
                                    print(result, flush=True)
                                connection.sendall(json.dumps(result).encode())
                            except (OSError, ValueError, KeyError, TypeError) as exc:
                                print(f'命令执行失败：{exc}', flush=True)
                finally:
                    print(relay.release(), flush=True)
                    remote.close()
                    SOCKET.unlink(missing_ok=True)
    return 0


def client(action):
    request = {'action': action, 'stamp': time.monotonic_ns()}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(str(SOCKET))
            connection.sendall(json.dumps(request).encode())
            print(json.loads(connection.recv(2048)), flush=True)
        return 0
    except (OSError, ValueError) as exc:
        print(f'Codex 按键服务不可用：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in {'serve', 'start', 'stop', 'send', 'status'}:
        raise SystemExit('用法：codex-ptt-key.py serve|start|stop|send|status')
    raise SystemExit(serve() if sys.argv[1] == 'serve' else client(sys.argv[1]))
