# SPDX-License-Identifier: GPL-3.0-only
"""设置窗口的只读状态与日志；复用通知探针，不连接设备、不执行修复。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import re

import status_notifier as probes
from remote_config import load_config, config_path, Settings

ROWS = {'bluetooth': '蓝牙连接', 'battery': '遥控器电量', 'hid': '遥控器按键设备', 'bridge': '音频桥接',
        'mic': 'SayAll 麦克风', 'keys': '按键服务', 'protection': '听写播放保护',
        'notifications': '桌面故障提示'}
UNITS = ('xiaomi-codex-status.service', 'sayall-bridge.service',
         'xiaomi-codex-keys.service', 'codex-remote-keys.service')


def service_active(unit):
    result = probes.run_readonly('systemctl', '--user', 'show', unit, '--property=ActiveState', '--value')
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip() == 'active'


def bluetooth_details(address):
    """同一次只读查询取得连接和电量；断线时不使用 BlueZ 缓存的旧电量。"""
    result = probes.run_readonly('bluetoothctl', 'info', address)
    if result is None or result.returncode != 0:
        return None, None
    match = re.search(r'^\s*Connected:\s*(yes|no)\s*$', result.stdout, re.M)
    connected = match.group(1) == 'yes' if match else None
    if connected is not True:
        return connected, None
    match = re.search(
        r'^\s*Battery Percentage:\s*(?:0x([0-9a-f]{1,4})(?:\s+\((\d{1,3})\))?|(\d{1,3}))\s*$',
        result.stdout, re.M | re.I)
    if not match:
        return connected, None
    hexadecimal, decimal, plain = match.groups()
    percentage = int(hexadecimal, 16) if hexadecimal is not None else int(plain)
    if not 0 <= percentage <= 100 or (decimal is not None and int(decimal) != percentage):
        return connected, None
    return connected, percentage


def panel_config():
    """兼容早期独立状态服务的配置，仅供界面只读检查。"""
    default_socket = probes.DEFAULT_SOCKET
    try:
        config = load_config()
    except ValueError:
        if os.environ.get('XIAOMI_CODEX_CONFIG') or config_path().exists():
            raise
        legacy = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'xiaomi-codex-status/config.json'
        config = Settings.from_dict(json.loads(legacy.read_text()))
        default_socket = probes.RUNTIME / 'xiaomi-codex/keys.sock'
    key_socket = Path(os.environ.get('XIAOMI_STATUS_KEY_SOCKET', str(default_socket)))
    if not key_socket.is_absolute():
        raise ValueError('状态查询 socket 必须为绝对路径')
    return config, key_socket


def collect_status():
    result = {'time': datetime.now().strftime('%H:%M:%S'), 'items': {}, 'battery_percent': None}
    try:
        config, key_socket = panel_config()
    except (OSError, ValueError):
        result['items'] = {key: ('unknown', '未配置，暂无法检查') for key in ROWS}
        return result
    tasks = {
        'bluetooth': lambda: bluetooth_details(config.remote_address),
        'hid': lambda: probes.hid_available(config),
        'bridge': probes.bridge_active,
        'mic': probes.microphone_state,
        'keys': lambda: probes.key_service_ready(key_socket),
        'protection': lambda: probes.inspect_protection(Path(config.dictation_helper))['status'],
        'notifications': lambda: service_active('xiaomi-codex-status.service'),
    }
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {key: pool.submit(task) for key, task in tasks.items()}
        values = {}
        for key, future in futures.items():
            try:
                values[key] = future.result()
            except Exception:
                values[key] = None
    connected, percentage = values['bluetooth'] or (None, None)
    values['bluetooth'] = connected
    if connected is True and type(percentage) is int and 0 <= percentage <= 100:
        result['battery_percent'] = percentage
        result['items']['battery'] = (('warning', f'{percentage}% · 电量偏低') if percentage <= 20
                                      else ('ok', f'{percentage}%'))
    else:
        reason = ('未连接，电量未知' if connected is False else
                  '设备尚未上报电量' if connected is True else '查询失败，电量未知')
        result['items']['battery'] = ('unknown', reason)
    texts = {'bluetooth': ('已连接', '未连接'), 'hid': ('可用', '未找到可读设备'),
             'bridge': ('运行中', '未运行 / 正在重连'), 'keys': ('已就绪', '未就绪'),
             'notifications': ('已启用', '未运行')}
    for key, (good, bad) in texts.items():
        value = values[key]
        level = 'ok' if value is True else 'warning' if value is False else 'unknown'
        result['items'][key] = (level, good if value is True else bad if value is False else '查询失败，状态未知')
    mic = values['mic']
    if mic == 'ok':
        result['items']['mic'] = ('ok', '设备可用')
    else:
        text = {'mic_missing': '未找到麦克风', 'mic_muted': '已静音', 'mic_zero': '输入音量为零'}.get(mic)
        result['items']['mic'] = ('warning', text) if text else ('unknown', '查询失败，状态未知')
    protection = values['protection']
    result['items']['protection'] = (
        ('ok', '保护已生效') if protection == 'protected' else
        ('warning', '需重新应用保护') if protection == 'known_original' else
        ('unknown', '客户端文件需要核实'))
    return result


def redact(text):
    text = re.sub(r'(?i)(?:[0-9a-f]{2}[:_-]){5}[0-9a-f]{2}', '<蓝牙地址>', text)
    text = re.sub(r'/home/[^/\s\"\']+', '~', text)
    return ''.join(ch for ch in text if ch in '\n\t' or ord(ch) >= 32)


def format_logs(raw, errors_only=True):
    lines = []
    for entry in raw.splitlines():
        try:
            record = json.loads(entry)
            if not isinstance(record, dict):
                continue
            message = record.get('MESSAGE', '')
            if not isinstance(message, str):
                continue
            try:
                event = json.loads(message)
            except ValueError:
                event = None
            try:
                is_error = int(record.get('PRIORITY', 6)) <= 4
            except (ValueError, TypeError):
                is_error = False
            if isinstance(event, dict) and event.get('code') in probes.HEALTH:
                is_error = event['code'] != 'ok'
                message = str(event.get('title', '')) + '：' + str(event.get('body', ''))
            else:
                is_error = is_error or bool(re.search(
                    r'error|warn|fail|exception|traceback|disconnect|unavailable|timeout|'
                    r'异常|失败|断开|不可用|超时|未就绪|未生效', message, re.I))
            if errors_only and not is_error:
                continue
            try:
                stamp = datetime.fromtimestamp(int(record['__REALTIME_TIMESTAMP']) / 1_000_000).strftime('%m-%d %H:%M:%S')
            except (KeyError, ValueError, TypeError, OverflowError, OSError):
                stamp = '时间未知'
            unit = str(record.get('_SYSTEMD_USER_UNIT', '遥控器')).removesuffix('.service')
            lines.append(f'{stamp}  [{unit}]\n{redact(message[:2000])}')
        except (ValueError, TypeError):
            continue
    return '\n\n'.join(lines) if lines else ('读取到的最近 200 条记录中，没有找到错误或警告。' if errors_only else '最近 24 小时暂无服务日志。')


def read_logs(errors_only=True):
    args = ['journalctl', '--user', '--no-pager', '--since=-24h', '-n', '200', '-o', 'json']
    for unit in UNITS:
        args += ['-u', unit]
    result = probes.run_readonly(*args)
    if result is None or result.returncode != 0:
        return '无法读取用户服务日志。请确认已登录桌面且拥有日志访问权限。'
    return format_logs(result.stdout, errors_only)
