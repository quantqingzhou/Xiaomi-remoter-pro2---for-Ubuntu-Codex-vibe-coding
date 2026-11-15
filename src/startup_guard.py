# SPDX-License-Identifier: GPL-3.0-only
"""桥接启动前等待依赖和 HID；只对已核实的目标遥控器做有限次重连。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import re
import struct
import time


@dataclass(frozen=True)
class Readiness:
    code: str
    message: str
    action: str | None = None


async def command(*args, timeout=4):
    """有超时、可取消的独立命令，不调用 shell，也不保留原始设备信息。"""
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, 'LC_ALL': 'C.UTF-8'})
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
        return process.returncode, output.decode('utf-8')
    except (OSError, UnicodeError, asyncio.TimeoutError):
        return None, ''
    finally:
        if process is not None and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()


def properties(text):
    return dict(re.findall(r'^\s*(Paired|Trusted|Blocked|Connected|Powered|Modalias):\s*(\S+)\s*$', text, re.M))


def input_state(config, sys_input=Path('/sys/class/input'), dev_input=Path('/dev/input')):
    denied = False
    for event in sys_input.glob('event*'):
        device = event / 'device'
        try:
            if ((device / 'uniq').read_text().strip().lower() != config.remote_address
                    or int((device / 'id/vendor').read_text().strip(), 16) != config.vendor_id
                    or int((device / 'id/product').read_text().strip(), 16) != config.product_id):
                continue
            words = (device / 'capabilities/key').read_text().split()
            index, bit = divmod(63, struct.calcsize('L') * 8)
            if len(words) <= index or not int(words[-1 - index], 16) & (1 << bit):
                continue
            if os.access(dev_input / event.name, os.R_OK):
                return 'ready'
            denied = True
        except (OSError, ValueError):
            continue
    return 'permission' if denied else 'missing'


async def probe(config, run=command, hid=input_state, accessible=os.access):
    # 用户会话依赖尚未就绪时只等待，不通过蓝牙重连掩盖音频或权限问题。
    if not accessible('/dev/uinput', os.R_OK | os.W_OK):
        return Readiness('uinput', '等待虚拟键盘权限；请检查 uinput 模块和设备访问规则')
    code, _ = await run('pactl', 'info')
    if code != 0:
        return Readiness('audio', '等待用户音频服务就绪')
    code, info = await run('bluetoothctl', '--timeout', '3', 'info', config.remote_address)
    if code != 0:
        return Readiness('bluetooth', '等待蓝牙服务和已配对的目标遥控器可查询')
    device = properties(info)
    if device.get('Paired') != 'yes' or device.get('Trusted') != 'yes' or device.get('Blocked') != 'no':
        return Readiness('pairing', '目标遥控器需已配对、信任且未被阻止；不会自动配对或更改信任设置')
    identity = re.search(r':v([0-9a-f]{4})p([0-9a-f]{4})', device.get('Modalias', ''), re.I)
    if not identity or (int(identity[1], 16), int(identity[2], 16)) != (config.vendor_id, config.product_id):
        return Readiness('identity', '蓝牙设备身份尚未核实，等待匹配的 VID/PID；不会对未知设备重连')
    state = hid(config)
    if state == 'permission':
        return Readiness('input_permission', '遥控器输入节点存在但不可读取；等待设备权限生效')
    if device.get('Connected') == 'yes':
        if state == 'ready':
            return Readiness('ready', '启动前检查通过：音频、权限和遥控器输入均已就绪')
        return Readiness('hid_missing', '蓝牙已连接但 HID 输入节点未出现，先等待初始化', 'reconnect')
    if device.get('Connected') != 'no':
        return Readiness('bluetooth', '蓝牙连接状态不明确，继续等待')
    code, adapter = await run('bluetoothctl', '--timeout', '3', 'show')
    if code != 0 or properties(adapter).get('Powered') != 'yes':
        return Readiness('adapter', '等待蓝牙适配器开启；不会自动打开用户关闭的蓝牙')
    return Readiness('disconnected', '遥控器尚未连接，等待唤醒并按退避间隔尝试连接', 'connect')


class RetryBudget:
    """一个等待周期中，每五分钟最多三次操作，其中重建连接最多两次。"""
    def __init__(self, now):
        self.code = None
        self.since = now
        self.window = now
        self.attempts = 0
        self.reconnects = 0
        self.next_attempt = now

    def due(self, state, now):
        if self.code != state.code:
            self.code, self.since = state.code, now
        if now - self.window >= 300:
            self.window, self.attempts, self.reconnects = now, 0, 0
        delay = 30 if state.action == 'reconnect' else 10
        if (state.action not in {'connect', 'reconnect'} or now - self.since < delay
                or now < self.next_attempt or self.attempts >= 3
                or (state.action == 'reconnect' and self.reconnects >= 2)):
            return False
        return True

    def reserve(self, action, now):
        self.attempts += 1
        self.reconnects += int(action == 'reconnect')
        self.next_attempt = now + (15, 30, 60)[min(self.attempts, 3) - 1]


async def wait_tick(stop, seconds):
    try:
        await asyncio.wait_for(stop.wait(), seconds)
    except asyncio.TimeoutError:
        pass


async def recover(config, action, stop, *, run=command, inspect=probe, pause=wait_tick, log=print):
    # 执行动作前重新查询：等待期间自然恢复的连接绝不能被旧状态再次断开。
    state = await inspect(config)
    if stop.is_set() or state.action != action:
        return
    if action == 'reconnect':
        log('输入节点持续缺失，仅重建当前遥控器连接；保留配对，不重置蓝牙适配器')
        await run('bluetoothctl', '--timeout', '8', 'disconnect', config.remote_address, timeout=10)
        await pause(stop, 2)
        if stop.is_set():
            return
        state = await inspect(config)
        if state.code == 'ready' or state.action != 'connect':
            return
    if stop.is_set():
        return
    log('尝试连接已配置的遥控器，稍后按实际 HID 状态判断结果')
    await run('bluetoothctl', '--timeout', '12', 'connect', config.remote_address, timeout=14)
    # connect 的返回码不替代设备和输入节点检查，下一轮 probe 再判断。


async def wait_for_ready(config, stop, *, inspect=probe, recovery=recover,
                         pause=wait_tick, clock=time.monotonic, log=print):
    budget = RetryBudget(clock())
    last_code = None
    while not stop.is_set():
        state = await inspect(config)
        if stop.is_set():
            return False
        now = clock()
        if state.code != last_code:
            log(state.message)
            last_code = state.code
        if state.code == 'ready':
            return True
        if budget.due(state, now):
            budget.reserve(state.action, now)
            await recovery(config, state.action, stop, log=log)
        await pause(stop, 3)
    return False


def guarded_bridge(base, config, supervise):
    """在上游创建麦克风前等待；SIGTERM 在等待期间也能正常停止。"""
    class StartupCheckedBridge(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._startup_stop = None
            self._shutdown_requested = False

        def request_stop(self):
            self._shutdown_requested = True
            if self._startup_stop is not None:
                self._startup_stop.set()
            super().request_stop()

        async def run(self, address=None, *args, **kwargs):
            # 拒绝命令行地址和配置地址不一致，避免检查一只遥控器却操作另一只。
            if address is not None and address.lower() != config.remote_address:
                raise ValueError('命令行遥控器地址与配置不一致')
            self._startup_stop = asyncio.Event()
            if self._shutdown_requested:
                return
            if not await wait_for_ready(config, self._startup_stop, log=self.log):
                return
            if self._shutdown_requested:
                return
            return await supervise(self, super().run(config.remote_address.upper(), *args, **kwargs))

    return StartupCheckedBridge
