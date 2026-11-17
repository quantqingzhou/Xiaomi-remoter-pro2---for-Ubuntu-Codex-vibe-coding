# SPDX-License-Identifier: GPL-3.0-only
"""模拟开机唤醒键、排队事件及抓取竞态；不操作真实输入设备。"""
import support
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from startup_guard import attach_released_keyboard


class Clock:
    now = 0.0
    def __call__(self): return self.now
    def sleep(self, seconds): self.now += seconds


class Device:
    path, name = 'test-device', 'test-remote'
    def __init__(self, clock, held_until=0, keys=(63,)):
        self.clock, self.held_until, self.keys = clock, held_until, keys
        self.grabbed = False
        self.grab_times = []
        self.closed = False
        self.buffer = []
        self.race = False
        self.disconnected = False
    def active_keys(self):
        if self.disconnected: raise OSError('disconnected')
        return list(self.keys) if self.clock() < self.held_until or (self.race and self.grabbed) else []
    def read(self):
        result, self.buffer = self.buffer, []
        if not result: raise BlockingIOError()
        return result
    def grab(self): self.grabbed = True; self.grab_times.append(self.clock())
    def ungrab(self): self.grabbed = False
    def close(self): self.closed = True


class IdleGrabTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.dev = Device(self.clock)
        self.hid = SimpleNamespace(running=True, dev=None, log=Mock(),
                                   find_keyboard=lambda: [self.dev], _ensure_uinput=Mock())
    def attach(self):
        return attach_released_keyboard(self.hid, clock=self.clock, sleep=self.clock.sleep)
    def test_boot_wake_hold_keeps_device_shared_until_release_has_settled(self):
        self.dev.held_until = .3
        self.assertTrue(self.attach())
        self.assertGreaterEqual(self.dev.grab_times[0], .45)
        self.assertIs(self.hid.dev, self.dev)
        self.assertFalse(self.dev.closed)
    def test_other_held_key_also_blocks_exclusive_grab(self):
        self.dev.keys, self.dev.held_until = (102,), 10
        self.assertFalse(self.attach())
        self.assertEqual(self.dev.grab_times, [])
        self.assertTrue(self.dev.closed)
        self.assertLess(self.clock(), 1.1)
    def test_already_delivered_events_are_not_replayed_after_attach(self):
        self.dev.buffer = [('F5', 1), ('F5', 0)]
        self.assertTrue(self.attach())
        self.assertEqual(self.dev.buffer, [])
        self.assertGreaterEqual(self.dev.grab_times[0], .17)
    def test_press_racing_with_grab_releases_device_for_retry(self):
        self.dev.race = True
        self.assertFalse(self.attach())
        self.assertFalse(self.dev.grabbed)
        self.assertTrue(self.dev.closed)
        self.assertIsNone(self.hid.dev)
    def test_disconnection_does_not_leave_open_device(self):
        self.dev.disconnected = True
        self.assertFalse(self.attach())
        self.assertTrue(self.dev.closed)
        self.assertIsNone(self.hid.dev)
    def test_stop_while_waiting_never_grabs(self):
        self.dev.held_until = 10
        def sleep(seconds):
            self.clock.sleep(seconds)
            if self.clock() >= .1: self.hid.running = False
        self.assertFalse(attach_released_keyboard(self.hid, clock=self.clock, sleep=sleep))
        self.assertEqual(self.dev.grab_times, [])
        self.assertTrue(self.dev.closed)
    def test_prepares_virtual_output_before_exclusive_grab(self):
        def prepare(device):
            self.assertFalse(device.grabbed)
            self.clock.sleep(2)
        self.hid._ensure_uinput = prepare
        self.assertTrue(self.attach())
        self.assertGreaterEqual(self.dev.grab_times[0], 2.15)
    def test_unused_candidate_is_closed(self):
        extra = Device(self.clock)
        self.hid.find_keyboard = lambda: [self.dev, extra]
        self.assertTrue(self.attach())
        self.assertTrue(extra.closed)


if __name__ == '__main__': unittest.main()
