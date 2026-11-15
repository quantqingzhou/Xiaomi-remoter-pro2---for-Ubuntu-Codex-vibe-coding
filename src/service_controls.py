# SPDX-License-Identifier: GPL-3.0-only
"""用户在设置窗口主动点击后的服务控制，仅操作本项目的用户服务。"""
import json
import os
from pathlib import Path
import subprocess

from keymap_config import atomic_write
import settings_status

BRIDGE = 'sayall-bridge.service'
NOTIFIER = 'xiaomi-codex-status.service'


def state_path():
    return Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'xiaomi-codex-remote/window-controls.json'


def read_preferences():
    try:
        value = json.loads(state_path().read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_preferences(value):
    atomic_write(state_path(), json.dumps(value).encode())


def key_unit():
    _config, path = settings_status.panel_config()
    if path == settings_status.probes.DEFAULT_SOCKET:
        return 'xiaomi-codex-keys.service'
    if path == settings_status.probes.RUNTIME / 'xiaomi-codex/keys.sock':
        return 'codex-remote-keys.service'
    raise ValueError('自定义按键服务路径，请使用自己的服务管理方式')


def enabled(unit=BRIDGE):
    result = settings_status.probes.run_readonly('systemctl', '--user', 'show', unit, '--property=UnitFileState', '--value')
    if result is None or result.returncode != 0:
        return None
    if not result.stdout.strip():
        loaded = settings_status.probes.run_readonly('systemctl', '--user', 'show', unit, '--property=LoadState', '--value')
        return False if loaded and loaded.returncode == 0 and loaded.stdout.strip() == 'not-found' else None
    return result.stdout.strip() in ('enabled', 'enabled-runtime')


def command(action, *units):
    if action not in ('start', 'stop', 'enable', 'disable') or any(
            unit not in (BRIDGE, NOTIFIER, 'xiaomi-codex-keys.service', 'codex-remote-keys.service') for unit in units):
        raise ValueError('不支持的服务操作')
    try:
        result = subprocess.run(['systemctl', '--user', action, *units], capture_output=True,
                                text=True, timeout=20)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('服务操作等待超时，系统可能仍在处理；请先查看状态再重试') from exc
    if result.returncode != 0:
        detail = settings_status.redact(result.stderr.strip())[:260]
        raise RuntimeError('操作未完成：' + (detail or '请查看状态与日志'))


def require_known(value):
    if type(value) is not bool:
        raise RuntimeError('无法确认通知服务状态，未继续操作；请稍后重试')
    return value


def verify(units, expected, query):
    if any(query(unit) is not expected for unit in units):
        raise RuntimeError('服务状态尚未达到预期，请查看状态与日志后重试')


def pause():
    keys = key_unit()
    state = read_preferences()
    notification_active = require_known(settings_status.service_active(NOTIFIER))
    # 停桥接失败后的再次暂停仍保留第一次的通知状态。
    state['resume_notifier'] = notification_active or (
        state.get('paused') is True and state.get('resume_notifier') is True)
    state['paused'] = True
    save_preferences(state)
    try:
        if notification_active:
            command('stop', NOTIFIER)
        command('stop', BRIDGE, keys)
        verify((NOTIFIER, BRIDGE, keys), False, settings_status.service_active)
    except RuntimeError as exc:
        raise RuntimeError(f'暂停尚未完成：{exc}；通知可能已停止，可点击“恢复运行”') from exc
    return '映射与遥控器麦克风已暂停；蓝牙原始按键仍可能响应。'


def resume():
    keys = key_unit()
    state = read_preferences()
    restore_notifier = (state.get('resume_notifier') is True if state.get('paused') is True
                        else require_known(enabled(NOTIFIER)))
    command('start', keys, BRIDGE)
    if restore_notifier is True:
        command('start', NOTIFIER)
    verify((keys, BRIDGE) + ((NOTIFIER,) if restore_notifier else ()), True, settings_status.service_active)
    state['paused'] = False
    state.pop('resume_notifier', None)
    save_preferences(state)
    return '已请求恢复运行，连接状态会自动更新。'


def set_autostart(active):
    key_unit()  # 未配置或不支持的服务不执行操作。
    state = read_preferences()
    if not active:
        notification_enabled = require_known(enabled(NOTIFIER))
        state['notification_autostart'] = notification_enabled or (
            state.get('autostart_disabled') is True and state.get('notification_autostart') is True)
        state['autostart_disabled'] = True
        save_preferences(state)
        units = (BRIDGE, NOTIFIER) if notification_enabled is True else (BRIDGE,)
        command('disable', *units)
        verify((BRIDGE, NOTIFIER), False, enabled)
        return '登录自启动已关闭；如需现在停止，请点“暂停后台”。'
    units = (BRIDGE, NOTIFIER) if state.get('notification_autostart') is True else (BRIDGE,)
    command('enable', *units)
    verify(units, True, enabled)
    state.pop('notification_autostart', None)
    state.pop('autostart_disabled', None)
    save_preferences(state)
    return '已开启登录自启动；如需现在运行，请点“恢复运行”。'
