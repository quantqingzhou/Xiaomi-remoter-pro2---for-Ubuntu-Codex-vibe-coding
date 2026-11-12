# SPDX-License-Identifier: GPL-3.0-only
"""从本地配置读取设置；源码不内置用户身份或设备地址。"""
from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
import re

DEFAULT_DICTATION_HELPER = '/usr/lib/chatgpt/resources/native/dictation-ducking'

def normalize_address(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', value):
        raise ValueError('remote_address 必须是蓝牙地址，例如 AA:BB:CC:DD:EE:FF')
    return value.lower()


def device_id(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError('设备标识必须是整数或十六进制字符串')
    result = int(value, 16) if isinstance(value, str) else int(value)
    if not 0 <= result <= 65535:
        raise ValueError('设备标识超出 16 位数值范围')
    return result


@dataclass(frozen=True)
class Settings:
    remote_address: str
    vendor_id: int = 0x2717
    product_id: int = 0x32B8
    codex_wm_classes: tuple = ('chatgpt',)
    sayall_bridge: str = '/usr/bin/sayall-bridge'
    sayall_config: str | None = None
    dictation_helper: str = DEFAULT_DICTATION_HELPER
    wechat_wm_classes: tuple = ()

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError('配置必须是 JSON 对象')
        allowed = {'remote_address', 'vendor_id', 'product_id', 'codex_wm_classes', 'sayall_bridge', 'sayall_config', 'dictation_helper', 'wechat_wm_classes'}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError('未知配置项：' + ', '.join(sorted(unknown)))
        address = normalize_address(data.get('remote_address'))
        classes = data.get('codex_wm_classes', ['chatgpt'])
        if not isinstance(classes, list) or not classes or not all(isinstance(x, str) and x.strip() for x in classes):
            raise ValueError('codex_wm_classes 必须是非空的窗口类名列表')
        wechat = data.get('wechat_wm_classes', [])
        if not isinstance(wechat, list) or not all(isinstance(x, str) and x.strip() for x in wechat):
            raise ValueError('wechat_wm_classes 必须是窗口类名列表；空列表表示关闭微信扩展')
        classes = tuple(x.strip().lower() for x in classes)
        wechat = tuple(x.strip().lower() for x in wechat)
        if set(classes) & set(wechat):
            raise ValueError('Codex 与微信的窗口类名不能重复')
        bridge = data.get('sayall_bridge', '/usr/bin/sayall-bridge')
        if not isinstance(bridge, str) or not Path(bridge).is_absolute() or '\n' in bridge or '\x00' in bridge:
            raise ValueError('sayall_bridge 必须是绝对路径')
        shared = data.get('sayall_config')
        if shared is not None and (not isinstance(shared, str) or not Path(shared).is_absolute()
                                   or '\n' in shared or '\x00' in shared):
            raise ValueError('sayall_config 必须是绝对路径或 null')
        helper = data.get('dictation_helper', DEFAULT_DICTATION_HELPER)
        if (not isinstance(helper, str) or not Path(helper).is_absolute()
                or any(c in helper for c in '\n\r\x00')
                or Path(helper).name != 'dictation-ducking' or Path(helper).parent.name != 'native'):
            raise ValueError('dictation_helper 必须是客户端 native/dictation-ducking 的绝对路径')
        return cls(address, device_id(data.get('vendor_id', '2717')),
                   device_id(data.get('product_id', '32b8')),
                   classes, bridge, shared, helper, wechat)

    def to_dict(self):
        return {'remote_address': self.remote_address, 'vendor_id': f'{self.vendor_id:04x}',
                'product_id': f'{self.product_id:04x}',
                'codex_wm_classes': list(self.codex_wm_classes), 'sayall_bridge': self.sayall_bridge,
                'sayall_config': self.sayall_config, 'dictation_helper': self.dictation_helper,
                'wechat_wm_classes': list(self.wechat_wm_classes)}


def config_path():
    override = os.environ.get('XIAOMI_CODEX_CONFIG')
    if override:
        return Path(override).expanduser()
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'xiaomi-codex-remote/config.json'


@lru_cache(maxsize=1)
def load_config():
    path = config_path()
    try:
        return Settings.from_dict(json.loads(path.read_text()))
    except FileNotFoundError as exc:
        raise ValueError(f'找不到配置：{path}。请先运行 scripts/manage.py install。') from exc

