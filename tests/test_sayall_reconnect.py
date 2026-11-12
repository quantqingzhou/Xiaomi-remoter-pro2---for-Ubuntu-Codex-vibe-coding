# SPDX-License-Identifier: GPL-3.0-only
"""BLE 连接监测测试；不访问蓝牙、音频或 systemd。"""
import support
import asyncio
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location(
    'reconnect', Path(os.environ.get('SAYALL_WRAPPER_PATH', str(Path(__file__).resolve().parents[1] / 'src' / 'sayall-reconnect.py'))))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_exits_and_cancels_original_run(self):
        bridge = SimpleNamespace(client=SimpleNamespace(is_connected=True))
        cleaned = asyncio.Event()
        async def run():
            try:
                bridge.client.is_connected = False
                await asyncio.Future()
            finally:
                cleaned.set()
        with self.assertRaisesRegex(ConnectionError, 'BLE 连接已断开'):
            await m.run_with_disconnect_guard(bridge, run(), .001)
        self.assertTrue(cleaned.is_set())

    async def test_initialization_without_client_is_allowed(self):
        bridge = SimpleNamespace(client=None)
        async def run():
            await asyncio.sleep(.01)
            bridge.client = SimpleNamespace(is_connected=True)
            await asyncio.sleep(.01)
            return 'ready'
        self.assertEqual(await m.run_with_disconnect_guard(bridge, run(), .001), 'ready')

    async def test_original_initialization_error_is_preserved(self):
        bridge = SimpleNamespace(client=None)
        async def run():
            raise RuntimeError('连接失败')
        with self.assertRaisesRegex(RuntimeError, '连接失败'):
            await m.run_with_disconnect_guard(bridge, run(), .001)

    async def test_normal_stop_does_not_become_disconnect_error(self):
        bridge = SimpleNamespace(client=SimpleNamespace(is_connected=True))
        stop = asyncio.Event()
        async def run():
            await stop.wait()
            return 0
        task = asyncio.create_task(m.run_with_disconnect_guard(bridge, run(), .001))
        await asyncio.sleep(.01)
        stop.set()
        self.assertEqual(await task, 0)
