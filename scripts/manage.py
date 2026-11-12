#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""预览和管理遥控器及客户端播放保护；不自动提权，也不自动重启服务。"""
import argparse
import base64
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))
from remote_config import Settings, DEFAULT_DICTATION_HELPER
from playback_protection import is_protected, protect, restore

NAME = 'xiaomi-codex-remote'
KEY_SERVICE = 'xiaomi-codex-keys.service'
DROPIN = '90-xiaomi-codex.conf'
RUNTIME_FILES = ['codex-ptt-key.py', 'sayall-reconnect.py', 'contextual_back.py', 'remote_config.py',
                 'playback_protection.py', 'no_duck_helper.py']
OWNED_FIELDS = ['address', 'voice_engine', 'voice_start_command', 'voice_stop_command', 'trigger_mode']


class ChineseArgumentParser(argparse.ArgumentParser):
    """仅翻译命令行帮助和参数错误，不改变参数名称或解析方式。"""

    def format_usage(self):
        return super().format_usage().replace('usage:', '用法：', 1)

    def format_help(self):
        text = super().format_help()
        for source, target in [
            ('usage:', '用法：'), ('positional arguments:', '位置参数：'),
            ('options:', '选项：'), ('optional arguments:', '选项：'),
            ('show this help message and exit', '显示帮助信息后退出'),
        ]:
            text = text.replace(source, target)
        return text

    def error(self, message):
        for source, target in [
            ('the following arguments are required:', '缺少必需参数：'),
            ('unrecognized arguments:', '无法识别的参数：'),
            ('invalid choice:', '无效选项：'), ('choose from', '可选值'),
            ('expected one argument', '需要一个参数值'),
            ('ignored explicit argument', '不接受显式参数值'),
            ('ambiguous option:', '选项不明确：'), ('could match', '可能匹配'),
            ('argument ', '参数 '),
        ]:
            message = message.replace(source, target)
        self.print_usage(sys.stderr)
        self.exit(2, f'{self.prog}：参数错误：{message}\n')


@dataclass(frozen=True)
class Layout:
    data: Path
    config: Path
    state: Path

    @classmethod
    def for_user(cls, home=None, environ=None):
        home = Path.home() if home is None else Path(home)
        env = os.environ if environ is None else environ
        values = [Path(env.get(key, home / suffix)) for key, suffix in
                  [('XDG_DATA_HOME', '.local/share'), ('XDG_CONFIG_HOME', '.config'),
                   ('XDG_STATE_HOME', '.local/state')]]
        if not all(p.is_absolute() for p in values):
            raise ValueError('XDG 目录必须使用绝对路径')
        return cls(*values)

    @property
    def manifest(self):
        return self.state / NAME / 'install-state.json'


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def unit_quote(value, executable=False):
    value = str(value)
    if any(c in value for c in '\n\r\x00'):
        raise ValueError('服务路径不能包含换行符或 NUL 字符')
    value = value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    if executable:
        value = value.replace('$', '$$')
    return '"' + value + '"'


def read_optional(path):
    if path.is_symlink():
        raise ValueError(f'拒绝替换符号链接：{path}')
    return path.read_bytes() if path.exists() else None


def read_object(raw):
    data = json.loads(raw) if raw is not None else {}
    if not isinstance(data, dict) or not isinstance(data.get('button_map', {}), dict):
        raise ValueError('SayAll 配置和 button_map 必须都是 JSON 对象')
    return data


def build_plan(layout, settings, python=None):
    # 保留虚拟环境或解释器的符号链接；解析真实路径可能导致找不到该环境的依赖包。
    python = Path(python or sys.executable).absolute()
    app = layout.data / NAME
    config = layout.config / NAME / 'config.json'
    sayall_config = layout.config / 'sayall-linux/config.json'
    settings = replace(settings, sayall_config=str(sayall_config))
    units = layout.config / 'systemd/user'
    if layout.manifest.exists():
        raise ValueError('此安装已由本程序管理；请先卸载，再重新安装')
    for existing in (units / 'sayall-bridge.service.d').glob('*.conf'):
        if existing.name != DROPIN and any(line.strip().startswith('ExecStart=') for line in existing.read_text().splitlines()):
            raise ValueError(f'发现已有的 SayAll 启动命令覆盖配置：{existing}。请先检查并迁移。')
    old = read_object(read_optional(sayall_config))
    if '158' in old.get('button_map', {}):
        raise ValueError('已有的 SayAll 返回键（158）映射与按窗口切换的退格功能冲突')
    changed = json.loads(json.dumps(old))
    command = lambda action: shlex.join(['/usr/bin/env', f'XIAOMI_CODEX_CONFIG={config}',
                                         str(python), '-B', str(app / 'codex-ptt-key.py'), action])
    changed.update(address=settings.remote_address.upper(), voice_engine='custom', trigger_mode='hid',
                   voice_start_command=command('start'), voice_stop_command=command('stop'))
    changed.setdefault('button_map', {})['28'] = {'single': 'command:' + command('send')}
    environment = 'Environment=' + unit_quote(f'XIAOMI_CODEX_CONFIG={config}')
    exec_line = lambda filename, action: ' '.join([unit_quote(python, True), '-B', unit_quote(app / filename, True), action])
    key_unit = f'''[Unit]
Description=用于 Codex 的小米遥控器虚拟键盘
After=graphical-session.target
PartOf=sayall-bridge.service

[Service]
Type=simple
{environment}
ExecStart={exec_line('codex-ptt-key.py', 'serve')}
Restart=on-failure
RestartSec=1
UMask=0077
'''
    dropin = f'''[Unit]
Wants={KEY_SERVICE}
After={KEY_SERVICE}

[Service]
{environment}
ExecStart=
ExecStart={exec_line('sayall-reconnect.py', 'run')}
'''
    rule = (f'SUBSYSTEM=="input", KERNEL=="event*", ATTRS{{id/vendor}}=="{settings.vendor_id:04x}", '
            f'ATTRS{{id/product}}=="{settings.product_id:04x}", '
            f'ATTRS{{uniq}}=="{settings.remote_address}", TAG+="uaccess"\n')
    files = {app / name: (REPO / 'src' / name).read_bytes() for name in RUNTIME_FILES}
    files.update({config: json_bytes(settings.to_dict()), sayall_config: json_bytes(changed),
                  units / KEY_SERVICE: key_unit.encode(),
                  units / 'sayall-bridge.service.d' / DROPIN: dropin.encode(),
                  layout.config / NAME / '70-xiaomi-codex-remote.rules': rule.encode(),
                  layout.config / NAME / '60-xiaomi-codex-uinput.rules':
                      b'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"\n',
                  layout.config / NAME / 'xiaomi-codex-uinput.conf': b'uinput\n'})
    for path in files:
        if path != sayall_config and read_optional(path) is not None:
            raise ValueError(f'目标位置已有未受本程序管理的文件，已停止以避免覆盖：{path}')
    return files, sayall_config


def atomic_write(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.new')
    # 不跟随预先存在的临时文件符号链接。
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def snapshot(path):
    value = read_optional(path)
    return {'before': base64.b64encode(value).decode() if value is not None else None,
            'mode': stat.S_IMODE(path.stat().st_mode) if value is not None else 0o600}


def apply_plan(layout, files, sayall_config):
    if layout.manifest.exists():
        raise ValueError('安装清单已存在')
    entries = {str(path): snapshot(path) | {'installed': base64.b64encode(content).decode(),
                                         'sha256': digest(content), 'sayall': path == sayall_config}
               for path, content in files.items()}
    manifest = {'version': 1, 'phase': 'prepared', 'files': entries}
    atomic_write(layout.manifest, json_bytes(manifest))
    written = []
    try:
        for path, content in files.items():
            atomic_write(path, content)
            written.append(path)
        manifest['phase'] = 'installed'
        atomic_write(layout.manifest, json_bytes(manifest))
    except BaseException:
        for path in reversed(written):
            entry = entries[str(path)]
            if entry['before'] is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, base64.b64decode(entry['before']), entry['mode'])
        layout.manifest.unlink(missing_ok=True)
        raise


def restore_sayall(current, before, installed):
    """仅恢复本程序管理的字段，保留用户后来对其他字段的修改。"""
    now, old, added = map(read_object, [current, before, installed])
    for key in OWNED_FIELDS:
        if now.get(key) != added[key]:
            raise ValueError(f'SayAll 字段在安装后被修改：{key}')
    if now.get('button_map', {}).get('28') != added['button_map']['28']:
        raise ValueError('SayAll 确认键（28）映射在安装后被修改')
    for key in OWNED_FIELDS:
        if key in old:
            now[key] = old[key]
        else:
            now.pop(key, None)
    if '28' in old.get('button_map', {}):
        now['button_map']['28'] = old['button_map']['28']
    else:
        now['button_map'].pop('28', None)
    if not now['button_map'] and 'button_map' not in old:
        now.pop('button_map')
    if now == old:
        return before
    return json_bytes(now)


def uninstall_plan(layout):
    manifest = json.loads(layout.manifest.read_text())
    if manifest.get('version') != 1:
        raise ValueError('不支持此安装清单版本')
    changes = {}
    for name, entry in manifest['files'].items():
        path = Path(name)
        if not path.is_absolute() or not any(path.resolve().is_relative_to(root.resolve()) for root in [layout.data / NAME, layout.config / NAME,
                   layout.config / 'systemd/user', layout.config / 'sayall-linux']):
            raise ValueError(f'安装清单中的路径超出管理范围：{path}')
        current = read_optional(path)
        before = base64.b64decode(entry['before']) if entry['before'] is not None else None
        installed = base64.b64decode(entry['installed'])
        if current == before:
            continue
        if entry['sayall'] and current is not None:
            restore = restore_sayall(current, before, installed)
        elif current is not None and digest(current) == entry['sha256']:
            restore = before
        else:
            raise ValueError(f'文件在安装后被修改，未移除任何文件：{path}')
        changes[path] = (restore, entry['mode'])
    return changes


def apply_uninstall(layout, changes):
    saved = {path: snapshot(path) for path in changes}
    written = []
    try:
        for path, (content, mode) in changes.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, content, mode)
            written.append(path)
        layout.manifest.unlink()
    except BaseException:
        for path in reversed(written):
            entry = saved[path]
            if entry['before'] is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, base64.b64decode(entry['before']), entry['mode'])
        raise


def require_stopped():
    for unit in ['sayall-bridge.service', KEY_SERVICE]:
        result = subprocess.run(['systemctl', '--user', 'is-active', unit], capture_output=True, text=True)
        if result.returncode == 0 or result.stdout.strip() in {'activating', 'deactivating', 'reloading'}:
            raise ValueError(f'应用更改前，请先停止 {unit}')
        if result.stdout.strip() not in {'inactive', 'failed', 'unknown'}:
            raise ValueError('无法检查用户服务；请在已登录的桌面会话中运行')


def main():
    parser = ChineseArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    install = sub.add_parser('install', help='默认只预览；添加 --apply 才会写入用户文件')
    install.add_argument('--address', required=True, metavar='蓝牙地址', help='已配对遥控器的蓝牙地址')
    install.add_argument('--sayall-bridge', default='/usr/bin/sayall-bridge', metavar='路径',
                         help='SayAll 桥接脚本的绝对路径（默认：/usr/bin/sayall-bridge）')
    install.add_argument('--dictation-helper', default=DEFAULT_DICTATION_HELPER, metavar='路径',
                         help='客户端 native/dictation-ducking 路径，安装及每次听写前都会检查播放保护')
    install.add_argument('--apply', action='store_true', help='执行安装计划；省略时只预览')
    uninstall = sub.add_parser('uninstall', help='预览恢复计划；添加 --apply 才会移除本程序管理的文件')
    uninstall.add_argument('--apply', action='store_true', help='执行卸载恢复计划；省略时只预览')
    for action, help_text in [('protect-client', '完整方案的必要步骤：停用已验证客户端的听写压音量功能'),
                              ('restore-client', '恢复客户端原有的听写压音量程序')]:
        client_parser = sub.add_parser(action, help=help_text)
        client_parser.add_argument('--helper', default=DEFAULT_DICTATION_HELPER, metavar='路径',
                                   help='客户端 native/dictation-ducking 的绝对路径')
        client_parser.add_argument('--apply', action='store_true', help='执行已预览的客户端文件更改；不会重启服务')
    args = parser.parse_args()
    layout = Layout.for_user()
    try:
        if args.action == 'install':
            settings = Settings.from_dict({'remote_address': args.address, 'sayall_bridge': args.sayall_bridge,
                                           'dictation_helper': args.dictation_helper})
            files, shared = build_plan(layout, settings)
            print('安装计划（将备份 SayAll 语音设置和确认键映射）：')
            for path in files: print('  写入', path)
            protected = is_protected(Path(settings.dictation_helper))
            print('客户端播放保护：' + ('已生效' if protected else '尚未生效，安装前必须完成 protect-client 步骤'))
            if args.apply:
                if os.geteuid() == 0:
                    raise ValueError('请使用桌面登录用户运行，不要使用 sudo')
                if not Path(settings.sayall_bridge).is_file():
                    raise ValueError('请先安装 SayAll 0.3.0，或指定其桥接脚本路径')
                if not protected:
                    raise ValueError('请先执行 protect-client --apply，停用客户端听写压音量；系统目录需要管理员权限')
                __import__('evdev'); __import__('bleak')
                require_stopped()
                apply_plan(layout, files, shared)
                print('用户文件已安装。请按 README 设置 udev 权限、F5 快捷键并启动服务。')
        elif args.action == 'uninstall':
            changes = uninstall_plan(layout)
            for path, (value, _) in changes.items(): print('  恢复' if value is not None else '  移除', path)
            if args.apply:
                require_stopped()
                apply_uninstall(layout, changes)
                print('用户文件已恢复。请运行 systemctl --user daemon-reload；若曾安装生成的 udev 规则，请手动移除。')
                print('客户端播放保护仍保留；完全卸载时，请再执行 restore-client 恢复客户端原文件。')
        else:
            operation = protect if args.action == 'protect-client' else restore
            result = operation(Path(args.helper), apply=args.apply)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        if not args.apply:
            print('当前仅为预览，未更改文件或服务。添加 --apply 后才会执行此计划。')
        return 0
    except (OSError, ValueError, ImportError) as exc:
        print(f'无法执行 {args.action}：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
