# SPDX-License-Identifier: GPL-3.0-only
"""模拟开机、断线、权限延迟及停止信号；不操作真实蓝牙或音频。"""
import support
import asyncio
from contextlib import ExitStack
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import startup_guard as guard
from remote_config import Settings

CONFIG = Settings.from_dict({'remote_address': 'AA:BB:CC:DD:EE:FF'})
INFO = '\n'.join(['Paired: yes', 'Trusted: yes', 'Blocked: no', 'Connected: yes',
                  'Modalias: bluetooth:v2717p32B8d00A4'])


class RetryTests(unittest.TestCase):
    def test_missing_hid_waits_30_seconds_and_limits_rebuilds(self):
        budget = guard.RetryBudget(0)
        state = guard.Readiness('hid_missing', '', 'reconnect')
        for now in (0, 10, 29):
            self.assertFalse(budget.due(state, now))
        self.assertTrue(budget.due(state, 30))
        budget.reserve('reconnect', 30)
        self.assertFalse(budget.due(state, 44))
        self.assertTrue(budget.due(state, 45))
        budget.reserve('reconnect', 45)
        self.assertFalse(budget.due(state, 200))
        self.assertTrue(budget.due(state, 300))

    def test_connect_attempts_back_off_and_have_window_limit(self):
        budget = guard.RetryBudget(0)
        state = guard.Readiness('disconnected', '', 'connect')
        self.assertFalse(budget.due(state, 0))
        for now, next_time in [(10, 25), (25, 55), (55, 115)]:
            self.assertTrue(budget.due(state, now))
            budget.reserve(state.action, now)
            self.assertEqual(budget.next_attempt, next_time)
        self.assertFalse(budget.due(state, 299))
        self.assertTrue(budget.due(state, 300))

    def test_state_changes_do_not_erase_global_budget(self):
        budget = guard.RetryBudget(0)
        for state, start, fire in [('disconnected', 0, 10), ('hid_missing', 11, 41)]:
            value = guard.Readiness(state, '', 'connect' if state == 'disconnected' else 'reconnect')
            self.assertFalse(budget.due(value, start))
            self.assertTrue(budget.due(value, fire))
            budget.reserve(value.action, fire)
        self.assertEqual(budget.attempts, 2)
        self.assertFalse(budget.due(guard.Readiness('input_permission', ''), 1000))

    def test_input_probe_distinguishes_missing_and_permission_without_grabbing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            device = root / 'sys/event1/device'
            (device / 'id').mkdir(parents=True)
            (device / 'capabilities').mkdir()
            (device / 'uniq').write_text(CONFIG.remote_address)
            (device / 'id/vendor').write_text('2717')
            (device / 'id/product').write_text('32b8')
            (device / 'capabilities/key').write_text('8000000000000000' if struct.calcsize('L') == 8 else '80000000 0')
            with patch.object(guard.os, 'access', return_value=False):
                self.assertEqual(guard.input_state(CONFIG, root/'sys', root/'dev'), 'permission')
            with patch.object(guard.os, 'access', return_value=True):
                self.assertEqual(guard.input_state(CONFIG, root/'sys', root/'dev'), 'ready')
            (device / 'id/product').write_text('0000')
            self.assertEqual(guard.input_state(CONFIG, root/'sys', root/'dev'), 'missing')


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def check(self, info=INFO, hid='ready', audio=0, powered='yes'):
        run = AsyncMock(side_effect=[(audio, ''), (0, info), (0, 'Powered: '+powered)])
        state = await guard.probe(CONFIG, run=run, hid=lambda _:hid, accessible=lambda *_:True)
        return state, run

    async def test_ready_device_does_not_need_adapter_queries_or_actions(self):
        state, run = await self.check()
        self.assertEqual(state.code, 'ready')
        self.assertIsNone(state.action)
        self.assertEqual(run.await_count, 2)

    async def test_dependencies_wait_without_reconnecting(self):
        run = AsyncMock()
        state = await guard.probe(CONFIG, run=run, accessible=lambda *_:False)
        self.assertEqual(state.code, 'uinput')
        run.assert_not_awaited()
        state, run = await self.check(audio=1)
        self.assertEqual(state.code, 'audio')
        self.assertIsNone(state.action)
        self.assertEqual(run.await_count, 1)

    async def test_unknown_unpaired_untrusted_blocked_or_wrong_identity_never_reconnects(self):
        for old, new, code in [('Paired: yes','Paired: no','pairing'),
                               ('Trusted: yes','Trusted: no','pairing'),
                               ('Blocked: no','Blocked: yes','pairing'),
                               ('v2717','v0000','identity'),
                               ('Modalias:', 'Missing:', 'identity'),
                               ('Connected: yes', 'Connected: unknown', 'bluetooth')]:
            with self.subTest(code=code, new=new):
                state, _ = await self.check(info=INFO.replace(old,new))
                self.assertEqual(state.code, code)
                self.assertIsNone(state.action)

    async def test_hid_permission_never_triggers_disconnect(self):
        state, _ = await self.check(hid='permission')
        self.assertEqual(state.code, 'input_permission')
        self.assertIsNone(state.action)

    async def test_connected_without_hid_can_rebuild_only_target(self):
        state, _ = await self.check(hid='missing')
        self.assertEqual(state.code, 'hid_missing')
        self.assertEqual(state.action, 'reconnect')

    async def test_disconnected_waits_if_adapter_is_off(self):
        info = INFO.replace('Connected: yes','Connected: no')
        state, _ = await self.check(info=info, powered='no')
        self.assertEqual(state.code, 'adapter')
        self.assertIsNone(state.action)
        state, _ = await self.check(info=info)
        self.assertEqual(state.action, 'connect')

    async def test_failed_bluetooth_query_has_no_repair_action(self):
        for response in [(1,''),(None,'')]:
            state = await guard.probe(CONFIG, run=AsyncMock(side_effect=[(0,''),response]),
                                      hid=lambda _:'ready', accessible=lambda *_:True)
            self.assertEqual(state.code, 'bluetooth')
            self.assertIsNone(state.action)


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_natural_recovery_before_action_does_not_disconnect(self):
        run = AsyncMock()
        await guard.recover(CONFIG, 'reconnect', asyncio.Event(), run=run,
                            inspect=AsyncMock(return_value=guard.Readiness('ready','')))
        run.assert_not_awaited()

    async def test_reconnect_only_uses_exact_address_and_preserves_pairing_and_adapter(self):
        run = AsyncMock(return_value=(0,''))
        inspect = AsyncMock(side_effect=[guard.Readiness('hid_missing','','reconnect'),
                                         guard.Readiness('disconnected','','connect')])
        await guard.recover(CONFIG, 'reconnect', asyncio.Event(), run=run, inspect=inspect,
                            pause=AsyncMock(), log=Mock())
        self.assertEqual([c.args for c in run.await_args_list],
                         [('bluetoothctl','--timeout','8','disconnect',CONFIG.remote_address),
                          ('bluetoothctl','--timeout','12','connect',CONFIG.remote_address)])

    async def test_cancel_after_disconnect_never_connects_again(self):
        stop = asyncio.Event()
        run = AsyncMock(return_value=(0,''))
        async def pause(*_): stop.set()
        await guard.recover(CONFIG,'reconnect',stop,run=run,
                            inspect=AsyncMock(return_value=guard.Readiness('hid_missing','','reconnect')),
                            pause=pause,log=Mock())
        self.assertEqual(run.await_count,1)

    async def test_permission_or_power_change_after_disconnect_does_not_force_connect(self):
        for code in ['ready','adapter','input_permission']:
            run=AsyncMock(return_value=(0,''))
            inspect=AsyncMock(side_effect=[guard.Readiness('hid_missing','','reconnect'),guard.Readiness(code,'')])
            await guard.recover(CONFIG,'reconnect',asyncio.Event(),run=run,inspect=inspect,pause=AsyncMock(),log=Mock())
            self.assertEqual(run.await_count,1)

    async def test_pre_cancelled_recovery_has_no_mutations(self):
        stop=asyncio.Event();stop.set()
        run=AsyncMock()
        await guard.recover(CONFIG,'connect',stop,run=run,
                            inspect=AsyncMock(return_value=guard.Readiness('disconnected','','connect')))
        run.assert_not_awaited()

    async def test_ready_gate_returns_without_retry_or_audio_creation(self):
        repair=AsyncMock()
        self.assertTrue(await guard.wait_for_ready(CONFIG,asyncio.Event(),
                        inspect=AsyncMock(return_value=guard.Readiness('ready','')),recovery=repair,log=Mock()))
        repair.assert_not_awaited()

    async def test_failed_connect_exit_code_does_not_prevent_accepting_real_ready_state(self):
        inspect=AsyncMock(side_effect=[guard.Readiness('disconnected','','connect'),
             guard.Readiness('disconnected','','connect'),guard.Readiness('ready','')])
        repair=AsyncMock(return_value=(1,'br-connection-page-timeout'))
        self.assertTrue(await guard.wait_for_ready(CONFIG,asyncio.Event(),inspect=inspect,
            recovery=repair,pause=AsyncMock(),clock=Mock(side_effect=[0,0,10,20]),log=Mock()))
        repair.assert_awaited_once()

    async def test_wait_gate_can_be_stopped_while_dependencies_are_missing(self):
        stop=asyncio.Event()
        async def pause(*_):stop.set()
        repair=AsyncMock()
        self.assertFalse(await guard.wait_for_ready(CONFIG,stop,
            inspect=AsyncMock(return_value=guard.Readiness('audio','')),recovery=repair,pause=pause,log=Mock()))
        repair.assert_not_awaited()


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    def base(self):
        class Base:
            def __init__(self):self.log=Mock();self.started=False;self.stop_requested=False
            def request_stop(self):self.stop_requested=True
            async def run(self,address,**kwargs):self.started=True;return address
        return Base

    async def test_upstream_only_starts_after_gate_passes(self):
        async def supervise(bridge,operation):return await operation
        bridge=guard.guarded_bridge(self.base(),CONFIG,supervise)()
        async def waiting(*_,**__):
            self.assertFalse(bridge.started)
            return True
        with patch.object(guard,'wait_for_ready',side_effect=waiting):
            self.assertEqual(await bridge.run(None),CONFIG.remote_address.upper())
        self.assertTrue(bridge.started)

    async def test_stop_during_gate_does_not_start_mic_or_ble(self):
        supervise=AsyncMock()
        bridge=guard.guarded_bridge(self.base(),CONFIG,supervise)()
        async def waiting(config,stop,**kwargs):
            bridge.request_stop()
            self.assertTrue(stop.is_set())
            return False
        with patch.object(guard,'wait_for_ready',side_effect=waiting):await bridge.run(None)
        self.assertFalse(bridge.started)
        self.assertTrue(bridge.stop_requested)
        supervise.assert_not_awaited()

    async def test_stop_before_run_and_conflicting_addresses_do_not_start(self):
        bridge=guard.guarded_bridge(self.base(),CONFIG,AsyncMock())()
        bridge.request_stop()
        await bridge.run(None)
        self.assertFalse(bridge.started)
        with self.assertRaisesRegex(ValueError,'地址与配置'):
            await bridge.run('11:22:33:44:55:66')

    async def test_command_timeout_kills_and_reaps_only_its_child(self):
        child=SimpleNamespace(returncode=None,kill=Mock(),communicate=AsyncMock(side_effect=[asyncio.TimeoutError(),(b'',b'')]))
        with patch.object(guard.asyncio,'create_subprocess_exec',return_value=child):
            self.assertEqual(await guard.command('fake-query'),(None,''))
        child.kill.assert_called_once()
        self.assertEqual(child.communicate.await_count,2)

    async def test_command_cancellation_is_not_swallowed_and_child_is_reaped(self):
        child=SimpleNamespace(returncode=None,kill=Mock(),communicate=AsyncMock(side_effect=[asyncio.CancelledError(),(b'',b'')]))
        with patch.object(guard.asyncio,'create_subprocess_exec',return_value=child),self.assertRaises(asyncio.CancelledError):
            await guard.command('fake-query')
        child.kill.assert_called_once()
        self.assertEqual(child.communicate.await_count,2)
