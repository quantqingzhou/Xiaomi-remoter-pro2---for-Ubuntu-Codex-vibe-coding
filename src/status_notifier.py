#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""独立的只读状态观察与中文桌面通知，不接管按键、不录音或修复系统。"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

from playback_protection import inspect_protection
from remote_config import load_config

POLL_SECONDS = 5
STARTUP_GRACE = 30
FAULT_DELAY = 10
RECOVERY_DELAY = 5
NOTIFICATION_INTERVAL = 30
RUNTIME = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))
DEFAULT_SOCKET = RUNTIME / 'xiaomi-codex-remote/keys.sock'


@dataclass(frozen=True)
class Health:
    code: str
    title: str
    body: str


HEALTH = {
    'ok': Health('ok', '小米遥控器：基础连接已恢复',
                 '遥控器输入、SayAll Mic 和播放保护检查正常。请继续用实际听写确认收音与转写。'),
    'disconnected': Health('disconnected', '小米遥控器：连接已断开',
                           '请轻按方向键唤醒，稍等自动重连；仍未恢复时，检查电量和系统蓝牙连接。'),
    'bluetooth_unknown': Health('bluetooth_unknown', '小米遥控器：无法检查蓝牙',
                               '蓝牙查询失败或超时。请检查系统蓝牙状态；这不代表已经确认遥控器断开。'),
    'hid_missing': Health('hid_missing', '小米遥控器：按键设备不可用',
                         '蓝牙已连接，但未找到可读取的遥控器键盘。请检查设备权限或重新连接遥控器。'),
    'bridge_down': Health('bridge_down', '小米遥控器：音频桥接未就绪',
                         'SayAll 桥接尚未运行或正在重连。若持续未恢复，请查看 sayall-bridge 服务日志。'),
    'bridge_unknown': Health('bridge_unknown', '小米遥控器：无法检查桥接',
                            '无法读取用户服务状态。请在已登录的图形桌面中检查 sayall-bridge 服务。'),
    'audio_unknown': Health('audio_unknown', '小米遥控器：无法检查麦克风',
                           '系统音频查询失败或超时。请查看系统声音设置；程序不会自动重启音频服务。'),
    'mic_missing': Health('mic_missing', '小米遥控器：找不到 SayAll Mic',
                         '桥接正在运行，但虚拟麦克风不存在。请检查 SayAll 桥接日志及系统输入设备。'),
    'mic_muted': Health('mic_muted', '小米遥控器：麦克风已静音',
                       'SayAll Mic 在系统中被静音。需要说话时，请在声音设置中取消它的静音。'),
    'mic_zero': Health('mic_zero', '小米遥控器：麦克风音量为零',
                      'SayAll Mic 的系统输入声道音量为零。请检查输入音量；程序不会替你调节。'),
    'keys_down': Health('keys_down', '小米遥控器：按键桥接未就绪',
                       '无法取得按键服务的就绪状态。请检查遥控器按键服务及其日志。'),
    'protection_original': Health('protection_original', '小米遥控器：听写播放保护未生效',
                                 '客户端可能在更新后恢复了原版辅助程序，遥控器会暂停 Codex 听写。请按项目的“客户端更新后恢复”指南重新检查并应用保护。'),
    'protection_unknown': Health('protection_unknown', '小米遥控器：客户端文件需要核实',
                                '播放保护文件内容未知或无法读取，遥控器会暂停 Codex 听写。请运行只读诊断；不要直接覆盖未知的新版本。'),
}


def run_readonly(*args):
    """调用方只传固定的查询命令；不通过 shell 执行，不将原始输出写入日志。"""
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                              timeout=3, env={**os.environ, 'LC_ALL': 'C.UTF-8'})
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return None


def bluetooth_connected(address, run=run_readonly):
    result = run('bluetoothctl', 'info', address)
    if result is None or result.returncode != 0:
        return None
    match = re.search(r'^\s*Connected:\s*(yes|no)\s*$', result.stdout, re.M)
    return match.group(1) == 'yes' if match else None


def hid_available(config, sys_input=Path('/sys/class/input'), dev_input=Path('/dev/input')):
    """只读 sysfs 身份与访问权限；不打开输入设备或获取独占权。"""
    for event in sys_input.glob('event*'):
        device = event / 'device'
        try:
            if ((device / 'uniq').read_text().strip().lower() == config.remote_address
                    and int((device / 'id/vendor').read_text().strip(), 16) == config.vendor_id
                    and int((device / 'id/product').read_text().strip(), 16) == config.product_id):
                # sysfs 位图从高位机器字开始排列；F5 的 Linux 键码为 63。
                words = (device / 'capabilities/key').read_text().split()
                bits = struct.calcsize('L') * 8
                word_index, bit = divmod(63, bits)
                if (len(words) > word_index and int(words[-1 - word_index], 16) & (1 << bit)
                        and os.access(dev_input / event.name, os.R_OK)):
                    return True
        except (OSError, ValueError):
            continue
    return False


def bridge_active(run=run_readonly):
    result = run('systemctl', '--user', 'show', 'sayall-bridge.service', '--property=ActiveState', '--value')
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip() == 'active'


def microphone_state(run=run_readonly):
    result = run('pactl', '--format=json', 'list', 'sources')
    if result is None or result.returncode != 0:
        return 'audio_unknown'
    try:
        sources = json.loads(result.stdout)
        if not isinstance(sources, list) or any(not isinstance(s, dict) for s in sources):
            return 'audio_unknown'
        mic = next((s for s in sources if s.get('name') == 'sayall-mic'), None)
        if mic is None:
            return 'mic_missing'
        if mic.get('mute') is True:
            return 'mic_muted'
        channels = mic.get('volume', {})
        if (isinstance(channels, dict) and channels
                and all(isinstance(v, dict) and type(v.get('value')) in (int, float)
                        and v['value'] == 0 for v in channels.values())):
            return 'mic_zero'
        # SUSPENDED 是空闲设备的正常状态；节点存在不能证明有真实语音信号。
        return 'ok'
    except (ValueError, TypeError):
        return 'audio_unknown'


def key_service_ready(path):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.5)
            connection.connect(str(path))
            connection.sendall(b'{"action":"status"}')
            # 当前协议的响应小于 2 KiB；允许流式 socket 将响应拆包。
            data = b''
            while len(data) < 4096:
                chunk = connection.recv(4096 - len(data))
                if not chunk:
                    break
                data += chunk
                try:
                    result = json.loads(data)
                except ValueError:
                    continue
                return isinstance(result, dict) and result.get('ready') is True
    except (OSError, ValueError):
        pass
    return False


def collect_health(config, key_socket=DEFAULT_SOCKET):
    """每次只报告最先确认的障碍，避免一次掉线产生多条连锁通知。"""
    connected = bluetooth_connected(config.remote_address)
    if connected is not True:
        return HEALTH['disconnected' if connected is False else 'bluetooth_unknown']
    if not hid_available(config):
        return HEALTH['hid_missing']
    active = bridge_active()
    if active is not True:
        return HEALTH['bridge_down' if active is False else 'bridge_unknown']
    mic = microphone_state()
    if mic != 'ok':
        return HEALTH[mic]
    if not key_service_ready(key_socket):
        return HEALTH['keys_down']
    protection = inspect_protection(Path(config.dictation_helper))['status']
    if protection != 'protected':
        return HEALTH['protection_original' if protection == 'known_original' else 'protection_unknown']
    return HEALTH['ok']


class NotificationGate:
    """开机宽限、故障去抖和通知限频；只有成功送达的故障才对应恢复通知。"""
    def __init__(self, now):
        self.started = now
        self.observed = None
        self.since = now
        self.reported = 'ok'
        self.next_attempt = now

    def candidate(self, health, now):
        if self.observed != health.code:
            # 同一次掉线可能依次表现为 HID、桥接和麦克风缺失。
            # 故障种类变化不能无限重置等待时间；恢复正常才切换计时周期。
            if self.observed in {None, 'ok'} or health.code == 'ok':
                self.since = now
            self.observed = health.code
        delay = RECOVERY_DELAY if health.code == 'ok' else FAULT_DELAY
        if (health.code == self.reported or now - self.started < STARTUP_GRACE
                or now - self.since < delay or now < self.next_attempt):
            return None
        return health

    def delivered(self, health, now, success):
        self.next_attempt = now + NOTIFICATION_INTERVAL
        if success:
            self.reported = health.code


class DesktopNotifier:
    def __init__(self, run=subprocess.run):
        self.run = run
        self.notification_id = None

    def send(self, health):
        args = ['notify-send', '--app-name=小米遥控器', '--icon=audio-input-microphone',
                '--urgency=normal', '--expire-time=8000', '--hint=boolean:suppress-sound:true', '--print-id']
        if self.notification_id is not None:
            args += ['--replace-id', str(self.notification_id)]
        args += ['--', health.title, health.body]
        try:
            result = self.run(args, capture_output=True, text=True, encoding='utf-8', timeout=3)
            if result.returncode == 0:
                value = result.stdout.strip()
                if value.isdecimal():
                    self.notification_id = int(value)
                return True
        except (OSError, UnicodeError, subprocess.SubprocessError):
            pass
        return False


def monitor(config, key_socket, stop, notifier, clock=time.monotonic):
    gate = NotificationGate(clock())
    last_status = None
    delivery_failed = False
    while not stop.is_set():
        health = collect_health(config, key_socket)
        now = clock()
        if health.code != last_status:
            print(json.dumps(asdict(health), ensure_ascii=False), flush=True)
            last_status = health.code
        candidate = gate.candidate(health, now)
        if candidate:
            success = notifier.send(candidate)
            gate.delivered(candidate, now, success)
            if not success and not delivery_failed:
                print('桌面通知暂未送达；请检查 notify-send、桌面通知权限及勿扰设置。', flush=True)
            delivery_failed = not success
        stop.wait(POLL_SECONDS)


def main(argv=None):
    class ChineseParser(argparse.ArgumentParser):
        def format_usage(self):
            return super().format_usage().replace('usage:', '用法：', 1)

        def format_help(self):
            return super().format_help().replace('usage:', '用法：', 1)

    parser = ChineseParser(description=__doc__, add_help=False)
    parser._optionals.title = '选项'
    parser.add_argument('-h', '--help', action='help', help='显示帮助信息后退出')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--once', action='store_true', help='只读检查一次，输出中文状态及退出码，不发送通知')
    modes.add_argument('--test-notification', action='store_true', help='发送一条明确标注的测试通知，不模拟故障')
    parser.add_argument('--key-socket', type=Path, default=DEFAULT_SOCKET,
                        help='按键服务 socket 的绝对路径，供已有原型使用')
    args = parser.parse_args(argv)
    if not args.key_socket.is_absolute():
        parser.error('--key-socket 必须是绝对路径')
    if args.test_notification:
        test = Health('test', '小米遥控器：通知测试', '这是一条测试通知。故障提示会在异常持续后出现，恢复后提示一次。')
        success = DesktopNotifier().send(test)
        print('测试通知已交给桌面系统。' if success else '测试通知发送失败，请检查 notify-send 和桌面会话。')
        return 0 if success else 1
    try:
        config = load_config()
        if args.once:
            health = collect_health(config, args.key_socket)
            print(json.dumps(asdict(health), ensure_ascii=False, indent=2))
            return 0 if health.code == 'ok' else 1
        directory = RUNTIME / 'xiaomi-codex-notifier'
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (directory / 'notifier.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            stop = threading.Event()
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            monitor(config, args.key_socket, stop, DesktopNotifier())
        return 0
    except (OSError, ValueError) as exc:
        print(f'状态提示无法启动：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
