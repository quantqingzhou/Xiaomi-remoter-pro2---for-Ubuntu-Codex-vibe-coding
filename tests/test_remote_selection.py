# SPDX-License-Identifier: GPL-3.0-only
import support
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from evdev import ecodes as E
from remote_config import Settings

spec=importlib.util.spec_from_file_location('selected_remote_wrapper',Path(__file__).resolve().parents[1]/'src/sayall-reconnect.py')
wrapper=importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)


class Candidate:
    def __init__(self,address,keys=None,fail=False):
        self.uniq=address
        self.info=SimpleNamespace(vendor=0x2717,product=0x32B8)
        self.keys=[E.KEY_F5] if keys is None else keys
        self.closed=False
        self.fail=fail
    def capabilities(self):
        if self.fail:raise SystemError('设备已移除')
        return {E.EV_KEY:self.keys}
    def close(self):self.closed=True


class RemoteSelectionTests(unittest.TestCase):
    def test_only_configured_remote_keyboard_is_kept(self):
        wrong=Candidate('00:00:00:00:00:01')
        consumer=Candidate('aa:bb:cc:dd:ee:ff',[E.KEY_ENTER])
        disappeared=Candidate('aa:bb:cc:dd:ee:ff',fail=True)
        correct=Candidate('AA:BB:CC:DD:EE:FF')
        base=type('Base',(),{'find_keyboard':staticmethod(lambda:[wrong,consumer,disappeared,correct])})
        settings=Settings.from_dict({'remote_address':'AA:BB:CC:DD:EE:FF'})
        cls=wrapper.contextual_remote_hid(base,lambda:True,settings)
        self.assertEqual(cls.find_keyboard(),[correct])
        self.assertTrue(all(x.closed for x in [wrong,consumer,disappeared]))
        self.assertFalse(correct.closed)
