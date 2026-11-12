# SPDX-License-Identifier: GPL-3.0-only
"""在临时目录验证客户端音量保护，不访问当前安装的客户端或音频设备。"""
import support
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import playback_protection as protection


class ProtectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.helper = self.root / 'native' / 'dictation-ducking'
        self.helper.parent.mkdir()
        # 仅使用自有测试内容；不在仓库中复制第三方原始脚本。
        self.original = '#!/usr/bin/python3\n# 测试原始辅助程序\n'.encode()
        self.original_mode = 0o751
        self.helper.write_bytes(self.original)
        self.helper.chmod(self.original_mode)
        self.original_stat = self.helper.stat()
        self.hash_patch = patch.object(protection, 'KNOWN_ORIGINAL_SHA256',
                                       hashlib.sha256(self.original).hexdigest())
        self.hash_patch.start()
        self.addCleanup(self.hash_patch.stop)
        self.backup = self.helper.with_name(self.helper.name + protection.BACKUP_SUFFIX)
        self.metadata = self.helper.with_name(self.helper.name + protection.METADATA_SUFFIX)

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
                for p in self.root.rglob('*') if p.is_file()}

    def assert_original(self):
        self.assertEqual(self.helper.read_bytes(), self.original)
        info = self.helper.stat()
        self.assertEqual(stat.S_IMODE(info.st_mode), self.original_mode)
        self.assertEqual((info.st_uid, info.st_gid),
                         (self.original_stat.st_uid, self.original_stat.st_gid))

    def test_preview_has_no_writes(self):
        before = self.snapshot()
        result = protection.protect(self.helper)
        self.assertEqual(result['status'], 'preview')
        self.assertFalse(result['changed'])
        self.assertEqual(before, self.snapshot())
        self.assertFalse(protection.is_protected(self.helper))

    def test_protect_and_restore_bytes_modes_and_ownership(self):
        result = protection.protect(self.helper, apply=True)
        self.assertEqual(result['status'], 'protected')
        self.assertTrue(result['changed'])
        self.assertTrue(protection.is_protected(self.helper))
        self.assertEqual(self.helper.read_bytes(),
                         Path(protection.__file__).with_name('no_duck_helper.py').read_bytes())
        self.assertEqual(stat.S_IMODE(self.helper.stat().st_mode), self.original_mode)
        self.assertEqual(self.backup.read_bytes(), self.original)
        self.assertEqual(stat.S_IMODE(self.backup.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.metadata.stat().st_mode), 0o600)
        meta = json.loads(self.metadata.read_text())
        self.assertEqual((meta['original_uid'], meta['original_gid']),
                         (self.original_stat.st_uid, self.original_stat.st_gid))
        self.assertEqual(meta['original_mode'], self.original_mode)
        result = protection.restore(self.helper, apply=True)
        self.assertEqual(result['status'], 'restored')
        self.assert_original()
        self.assertFalse(protection.is_protected(self.helper))
        self.assertTrue(self.backup.exists())
        self.assertTrue(self.metadata.exists())

    def test_unknown_original_is_rejected_without_writes(self):
        self.helper.write_bytes(b'unknown-client-version')
        before = self.snapshot()
        with self.assertRaisesRegex(protection.ProtectionError, '不是已核实版本'):
            protection.protect(self.helper, apply=True)
        self.assertEqual(before, self.snapshot())

    def test_wrong_paths_and_nonregular_file_are_rejected(self):
        for value in [Path('native/dictation-ducking'), self.root / 'dictation-ducking',
                      self.helper.with_name('another-helper')]:
            with self.subTest(value=value), self.assertRaises(protection.ProtectionError):
                protection.protect(value, apply=True)
            self.assertFalse(protection.is_protected(value))
        self.helper.unlink()
        self.helper.mkdir()
        with self.assertRaisesRegex(protection.ProtectionError, '普通文件'):
            protection.protect(self.helper, apply=True)

    def test_helper_symlink_is_rejected(self):
        outside = self.root / 'original'
        self.helper.rename(outside)
        self.helper.symlink_to(outside)
        with self.assertRaisesRegex(protection.ProtectionError, '符号链接'):
            protection.protect(self.helper, apply=True)
        self.assertFalse(protection.is_protected(self.helper))
        self.assertEqual(outside.read_bytes(), self.original)
        self.assertFalse(self.backup.exists())

    def test_backup_symlink_is_rejected(self):
        outside = self.root / 'original'
        outside.write_bytes(self.original)
        self.backup.symlink_to(outside)
        with self.assertRaisesRegex(protection.ProtectionError, '符号链接'):
            protection.protect(self.helper, apply=True)
        self.assert_original()
        self.assertEqual(outside.read_bytes(), self.original)

    def test_repeat_protection_keeps_original_backup(self):
        protection.protect(self.helper, apply=True)
        before = self.snapshot()
        backup_info = self.backup.stat()
        result = protection.protect(self.helper, apply=True)
        self.assertEqual(result['status'], 'already_protected')
        self.assertFalse(result['changed'])
        self.assertEqual(before, self.snapshot())
        self.assertEqual((backup_info.st_ino, backup_info.st_mtime_ns),
                         (self.backup.stat().st_ino, self.backup.stat().st_mtime_ns))

    def test_restore_preview_has_no_writes(self):
        protection.protect(self.helper, apply=True)
        before = self.snapshot()
        self.assertEqual(protection.restore(self.helper)['status'], 'preview')
        self.assertEqual(before, self.snapshot())

    def test_modified_client_is_not_overwritten_on_restore(self):
        protection.protect(self.helper, apply=True)
        self.helper.write_bytes(b'new-client-version')
        before = self.snapshot()
        with self.assertRaisesRegex(protection.ProtectionError, '已更新'):
            protection.restore(self.helper, apply=True)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(protection.is_protected(self.helper))

    def test_corrupt_backup_is_not_restored(self):
        protection.protect(self.helper, apply=True)
        self.backup.write_bytes(b'corrupt-backup')
        before = self.snapshot()
        with self.assertRaisesRegex(protection.ProtectionError, '校验失败'):
            protection.restore(self.helper, apply=True)
        self.assertEqual(before, self.snapshot())

    def test_changed_metadata_or_file_mode_is_not_trusted(self):
        protection.protect(self.helper, apply=True)
        record = json.loads(self.metadata.read_text())
        record['original_mode'] = 0o777
        self.metadata.write_text(json.dumps(record))
        with self.assertRaisesRegex(protection.ProtectionError, '权限或属主已改变'):
            protection.restore(self.helper, apply=True)
        self.assertTrue(protection.is_protected(self.helper))

    def test_publicly_readable_backup_is_rejected(self):
        protection.protect(self.helper, apply=True)
        self.backup.chmod(0o644)
        with self.assertRaisesRegex(protection.ProtectionError, '私有权限'):
            protection.restore(self.helper, apply=True)

    def test_metadata_write_failure_cleans_new_backups(self):
        real_write = protection._write_atomic
        def fail(path, *args, **kwargs):
            if path == self.metadata:
                raise OSError('模拟元数据写入失败')
            return real_write(path, *args, **kwargs)
        with patch.object(protection, '_write_atomic', fail), self.assertRaises(OSError):
            protection.protect(self.helper, apply=True)
        self.assert_original()
        self.assertEqual(list(self.helper.parent.iterdir()), [self.helper])

    def test_target_write_failure_restores_original_and_cleans_backups(self):
        real_write = protection._write_atomic
        def fail(path, *args, **kwargs):
            if path == self.helper:
                raise OSError('模拟目标写入失败')
            return real_write(path, *args, **kwargs)
        with patch.object(protection, '_write_atomic', fail), self.assertRaises(OSError):
            protection.protect(self.helper, apply=True)
        self.assert_original()
        self.assertEqual(list(self.helper.parent.iterdir()), [self.helper])

    def test_failure_after_target_replacement_rolls_back(self):
        real_write = protection._write_atomic
        failed = False
        def fail_after(path, *args, **kwargs):
            nonlocal failed
            real_write(path, *args, **kwargs)
            if path == self.helper and not failed:
                failed = True
                raise OSError('模拟原子替换后的写入失败')
        with patch.object(protection, '_write_atomic', fail_after), self.assertRaises(OSError):
            protection.protect(self.helper, apply=True)
        self.assert_original()
        self.assertEqual(list(self.helper.parent.iterdir()), [self.helper])

    def test_failed_restore_retains_protection_and_backup(self):
        protection.protect(self.helper, apply=True)
        before = self.snapshot()
        real_write = protection._write_atomic
        failed = False
        def fail_after(path, *args, **kwargs):
            nonlocal failed
            real_write(path, *args, **kwargs)
            if path == self.helper and not failed:
                failed = True
                raise OSError('模拟恢复提交后的失败')
        with patch.object(protection, '_write_atomic', fail_after), self.assertRaises(OSError):
            protection.restore(self.helper, apply=True)
        self.assertTrue(protection.is_protected(self.helper))
        self.assertEqual(before, self.snapshot())

    def test_reprotect_after_restore_reuses_private_backup(self):
        protection.protect(self.helper, apply=True)
        before = self.backup.stat()
        protection.restore(self.helper, apply=True)
        protection.protect(self.helper, apply=True)
        self.assertTrue(protection.is_protected(self.helper))
        self.assertEqual((before.st_ino, before.st_mtime_ns),
                         (self.backup.stat().st_ino, self.backup.stat().st_mtime_ns))

    def test_protected_preview_does_not_require_backup_read_access(self):
        protection.protect(self.helper, apply=True)
        with patch.object(protection, '_validate_backup', side_effect=PermissionError('私有备份')):
            result = protection.protect(self.helper)
        self.assertEqual(result['status'], 'already_protected')

    def test_is_protected_requires_exact_replacement_bytes(self):
        protection.protect(self.helper, apply=True)
        self.helper.write_bytes(self.helper.read_bytes() + b'\n')
        self.assertFalse(protection.is_protected(self.helper))


class QuietHelperTests(unittest.TestCase):
    def test_waits_for_stdin_eof_without_running_audio_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'unexpected-tool-call'
            for command in ['pw-cli', 'pw-dump', 'pactl', 'wpctl', 'pacat', 'pw-cat']:
                tool = root / command
                tool.write_text('#!/bin/sh\nprintf called > "' + str(marker) + '"\nexit 91\n')
                tool.chmod(0o755)
            helper = root / 'quiet-helper'
            helper.write_bytes(Path(protection.__file__).with_name('no_duck_helper.py').read_bytes())
            helper.chmod(0o755)
            env = dict(os.environ, PATH=str(root), PYTHONDONTWRITEBYTECODE='1')
            proc = subprocess.Popen([str(helper)], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            try:
                time.sleep(.08)
                self.assertIsNone(proc.poll(), '标准输入仍打开时应保持运行')
                proc.stdin.write(b'keep-open')
                proc.stdin.flush()
                time.sleep(.05)
                self.assertIsNone(proc.poll(), '控制字节不应让程序提前退出')
                stdout, stderr = proc.communicate(timeout=2)
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(stdout, b'')
                self.assertEqual(stderr, b'')
                self.assertFalse(marker.exists(), '辅助程序不得调用任何音频命令')
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate(timeout=2)
