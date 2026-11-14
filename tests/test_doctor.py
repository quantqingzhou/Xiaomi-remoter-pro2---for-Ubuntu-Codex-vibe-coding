# SPDX-License-Identifier: GPL-3.0-only
"""客户端专用诊断不依赖已安装桥接，不触碰音频或蓝牙。"""
import support
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import doctor
import playback_protection as protection


class ClientDoctorTests(unittest.TestCase):
    def test_client_only_states_without_bridge_config_or_system_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / 'native' / 'dictation-ducking'
            helper.parent.mkdir()
            original = b'test-client-original'
            with patch.object(protection, 'KNOWN_ORIGINAL_SHA256', hashlib.sha256(original).hexdigest()), \
                 patch.object(doctor, 'load_config', side_effect=AssertionError('不应读取桥接配置')), \
                 patch.object(doctor.subprocess, 'run', side_effect=AssertionError('不应调用系统命令')):
                for content, state, code in [(original, 'known_original', 1),
                                             (protection._replacement(), 'protected', 0),
                                             (b'unknown', 'unknown', 1)]:
                    with self.subTest(state=state):
                        helper.write_bytes(content)
                        out = io.StringIO()
                        with redirect_stdout(out):
                            result = doctor.main(['--client-only', '--helper', str(helper)])
                        self.assertEqual(result, code)
                        self.assertIn('保护状态：' + state, out.getvalue())
                        self.assertIn(hashlib.sha256(content).hexdigest(), out.getvalue())
                        self.assertEqual(helper.read_bytes(), content)
                        self.assertEqual(list(helper.parent.iterdir()), [helper])

    def test_client_only_missing_target_returns_failure(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as out:
            result = doctor.main(['--client-only', '--helper', str(Path(tmp) / 'native/dictation-ducking')])
        self.assertEqual(result, 1)
        self.assertIn('保护状态：unavailable', out.getvalue())

    def test_custom_helper_requires_client_only(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            doctor.main(['--helper', '/example/native/dictation-ducking'])
        self.assertEqual(raised.exception.code, 2)
