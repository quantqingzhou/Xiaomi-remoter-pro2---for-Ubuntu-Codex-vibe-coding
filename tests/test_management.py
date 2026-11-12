# SPDX-License-Identifier: GPL-3.0-only
import support
import base64
from dataclasses import replace
import json
import io
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

import manage
from remote_config import Settings, normalize_address


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.layout = manage.Layout.for_user(Path(self.temp.name) / '测试 用户', {})
        self.settings = Settings.from_dict({'remote_address': 'AA:BB:CC:DD:EE:FF'})
        self.shared = self.layout.config / 'sayall-linux/config.json'

    def old_config(self, value):
        self.shared.parent.mkdir(parents=True, exist_ok=True)
        self.shared.write_bytes(manage.json_bytes(value))

    def plan(self):
        return manage.build_plan(self.layout, self.settings, '/usr/bin/python3')

    def install(self):
        files, shared = self.plan()
        manage.apply_plan(self.layout, files, shared)
        return files

    def test_preview_has_no_writes(self):
        files, shared = self.plan()
        self.assertFalse(self.layout.config.exists())
        self.assertFalse(self.layout.data.exists())
        self.assertFalse(self.layout.state.exists())
        self.assertIn(shared, files)

    def test_runtime_contains_mandatory_playback_protection(self):
        files, shared = self.plan()
        app = self.layout.data / manage.NAME
        self.assertIn(app / 'playback_protection.py', files)
        self.assertIn(app / 'no_duck_helper.py', files)
        settings = json.loads(files[self.layout.config / manage.NAME / 'config.json'])
        self.assertEqual(settings['dictation_helper'], self.settings.dictation_helper)

    def test_cli_install_without_protection_never_writes_or_stops_services(self):
        fake_sayall = Path(self.temp.name) / 'sayall-bridge'
        fake_sayall.write_text('# 临时依赖文件，仅用于安装预检测试\n')
        args = ['manage.py', 'install', '--address', 'AA:BB:CC:DD:EE:FF',
                '--sayall-bridge', str(fake_sayall), '--apply']
        with patch.object(sys, 'argv', args), patch.object(manage.Layout, 'for_user', return_value=self.layout), \
                patch.object(manage.os, 'geteuid', return_value=1000), \
                patch.object(manage, 'is_protected', return_value=False), \
                patch.object(manage, 'require_stopped') as service_check, \
                patch.object(manage, 'apply_plan') as apply, redirect_stdout(io.StringIO()), \
                redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(manage.main(), 1)
        self.assertIn('protect-client', errors.getvalue())
        service_check.assert_not_called()
        apply.assert_not_called()
        self.assertFalse(self.layout.data.exists())
        self.assertFalse(self.layout.config.exists())

    def test_client_protection_command_defaults_to_preview(self):
        args = ['manage.py', 'protect-client']
        with patch.object(sys, 'argv', args), patch.object(manage, 'protect', return_value={'message': '预览'}) as operation, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(manage.main(), 0)
        operation.assert_called_once_with(Path(manage.DEFAULT_DICTATION_HELPER), apply=False)

    def test_commands_safely_quote_spaces_dollars_and_shell_syntax(self):
        self.layout = manage.Layout.for_user(Path(self.temp.name) / "a $USER %x ' $(false)", {})
        files, shared = self.plan()
        command = json.loads(files[shared])['voice_start_command']
        parts = shlex.split(command)
        self.assertEqual(parts[-1], 'start')
        self.assertEqual(parts[-2], str(self.layout.data / manage.NAME / 'codex-ptt-key.py'))
        self.assertEqual(parts[1], 'XIAOMI_CODEX_CONFIG=' + str(self.layout.config / manage.NAME / 'config.json'))
        unit = files[self.layout.config / 'systemd/user' / manage.KEY_SERVICE].decode()
        self.assertIn('%%x', unit)
        self.assertIn('$$USER', unit)

    def test_interpreter_symlink_is_preserved_for_virtualenvs(self):
        interpreter=Path(self.temp.name)/'venv/bin/python'
        interpreter.parent.mkdir(parents=True)
        interpreter.symlink_to('/usr/bin/python3')
        files,shared=manage.build_plan(self.layout,self.settings,str(interpreter))
        command=shlex.split(json.loads(files[shared])['voice_start_command'])
        self.assertEqual(command[2],str(interpreter))

    def test_install_and_uninstall_restore_original_bytes(self):
        self.old_config({'voice_engine': 'vokie', 'gain_db': 4, 'button_map': {'28': {'single': 'key:ENTER'}, '102': 'passthrough'}})
        original = self.shared.read_bytes()
        files = self.install()
        installed = json.loads(self.shared.read_text())
        self.assertEqual(installed['gain_db'], 4)
        self.assertEqual(installed['button_map']['102'], 'passthrough')
        self.assertEqual(installed['trigger_mode'], 'hid')
        manage.apply_uninstall(self.layout, manage.uninstall_plan(self.layout))
        self.assertEqual(self.shared.read_bytes(), original)
        self.assertFalse(self.layout.manifest.exists())
        for p in files:
            if p != self.shared: self.assertFalse(p.exists())

    def test_fresh_install_uninstall_removes_generated_config(self):
        self.install()
        manage.apply_uninstall(self.layout, manage.uninstall_plan(self.layout))
        self.assertFalse(self.shared.exists())

    def test_uninstall_preserves_later_unrelated_settings(self):
        self.old_config({'gain_db': 1})
        self.install()
        data = json.loads(self.shared.read_text())
        data['gain_db'] = 5
        data['button_map']['102'] = 'key:HOME'
        self.shared.write_bytes(manage.json_bytes(data))
        manage.apply_uninstall(self.layout, manage.uninstall_plan(self.layout))
        self.assertEqual(json.loads(self.shared.read_text()), {'gain_db': 5, 'button_map': {'102': 'key:HOME'}})

    def test_user_modified_owned_field_blocks_uninstall_before_any_changes(self):
        files = self.install()
        data = json.loads(self.shared.read_text()); data['voice_start_command'] = 'my-new-command'
        self.shared.write_bytes(manage.json_bytes(data))
        before = {p:p.read_bytes() for p in files}
        with self.assertRaisesRegex(ValueError, '在安装后被修改'):
            manage.uninstall_plan(self.layout)
        self.assertEqual({p:p.read_bytes() for p in files}, before)

    def test_existing_override_and_back_mapping_are_rejected(self):
        self.old_config({'button_map': {'158': 'key:ESC'}})
        with self.assertRaisesRegex(ValueError, '返回键'):
            self.plan()
        self.shared.unlink()
        foreign = self.layout.config / 'systemd/user/sayall-bridge.service.d/custom.conf'
        foreign.parent.mkdir(parents=True);foreign.write_text('[Service]\nExecStart=/my/bridge\n')
        with self.assertRaisesRegex(ValueError, '启动命令覆盖配置'):
            self.plan()

    def test_partial_write_failure_restores_preinstall_state(self):
        self.old_config({'voice_engine': 'vokie'})
        original = self.shared.read_bytes()
        files, shared = self.plan()
        real_write = manage.atomic_write
        counter = 0
        def interrupted(*args, **kwargs):
            nonlocal counter
            counter += 1
            if counter == 4: raise OSError('模拟写入失败')
            return real_write(*args, **kwargs)
        with patch.object(manage, 'atomic_write', interrupted):
            with self.assertRaisesRegex(OSError, '模拟'):
                manage.apply_plan(self.layout, files, shared)
        self.assertEqual(self.shared.read_bytes(), original)
        self.assertFalse(self.layout.manifest.exists())
        self.assertFalse(any(p.exists() for p in files if p != shared))

    def test_failure_after_shared_config_write_restores_original(self):
        self.old_config({'voice_engine':'vokie','button_map':{'28':'passthrough'}})
        original=self.shared.read_bytes()
        files,shared=self.plan()
        real_write=manage.atomic_write
        def fail_after_shared(path,*args,**kwargs):
            if path.name==manage.KEY_SERVICE:
                raise OSError('共享配置写入后发生模拟故障')
            return real_write(path,*args,**kwargs)
        with patch.object(manage,'atomic_write',fail_after_shared),self.assertRaises(OSError):
            manage.apply_plan(self.layout,files,shared)
        self.assertEqual(shared.read_bytes(),original)
        self.assertFalse(self.layout.manifest.exists())
        self.assertFalse(any(p.exists() for p in files if p!=shared))

    def test_xdg_paths_are_written_into_runtime_and_shared_settings(self):
        env = {key: str(Path(self.temp.name) / key) for key in ['XDG_DATA_HOME','XDG_CONFIG_HOME','XDG_STATE_HOME']}
        self.layout = manage.Layout.for_user(Path(self.temp.name), env)
        files, shared = self.plan()
        cfg = Settings.from_dict(json.loads(files[self.layout.config / manage.NAME / 'config.json']))
        self.assertEqual(cfg.sayall_config, str(shared))
        self.assertEqual(shared.parent.parent, Path(env['XDG_CONFIG_HOME']))

    def test_udev_rule_is_scoped_to_selected_remote(self):
        files, _ = self.plan()
        rule = files[self.layout.config / manage.NAME / '70-xiaomi-codex-remote.rules'].decode()
        self.assertIn('aa:bb:cc:dd:ee:ff', rule)
        self.assertIn('2717', rule)
        self.assertIn('32b8', rule)
        self.assertNotIn('MODE="0666"', rule)

    def test_manifest_path_escape_is_rejected(self):
        self.install()
        data=json.loads(self.layout.manifest.read_text())
        entry=next(iter(data['files'].values()))
        data['files']={str(self.layout.config/'systemd/user/../../../../outside'):entry}
        self.layout.manifest.write_bytes(manage.json_bytes(data))
        with self.assertRaisesRegex(ValueError, '超出管理范围'):
            manage.uninstall_plan(self.layout)

    def test_symlink_and_existing_unmanaged_file_are_rejected(self):
        destination=self.layout.data/manage.NAME/'codex-ptt-key.py'
        destination.parent.mkdir(parents=True)
        destination.symlink_to(Path(self.temp.name)/'other')
        with self.assertRaisesRegex(ValueError, '符号链接'):
            self.plan()
        destination.unlink();destination.write_text('非本程序文件')
        with self.assertRaisesRegex(ValueError, '未受本程序管理'):
            self.plan()


class ConfigurationTests(unittest.TestCase):
    def test_valid_settings_roundtrip(self):
        cfg=Settings.from_dict({'remote_address':'AA:BB:CC:DD:EE:FF'})
        self.assertEqual(Settings.from_dict(cfg.to_dict()),cfg)

    def test_invalid_addresses_and_ids_rejected(self):
        for value in ['', 'not a MAC', 'AA:BB:CC:DD:EE:FF\n', 'AA:BB:CC:DD:EE:*']:
            with self.subTest(value=value), self.assertRaises(ValueError): normalize_address(value)
        for value in [True, -1, 65536, 1.5]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                Settings.from_dict({'remote_address':'AA:BB:CC:DD:EE:FF','vendor_id':value})

    def test_unknown_fields_and_empty_focus_classes_rejected(self):
        for extra in [{'remtoe_address':'typo'}, {'codex_wm_classes':[]}]:
            with self.assertRaises(ValueError):
                Settings.from_dict({'remote_address':'AA:BB:CC:DD:EE:FF',**extra})
