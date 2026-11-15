# SPDX-License-Identifier: GPL-3.0-only
"""独立按键配置：严格校验、原子保存和运行时读取，不管理音频。"""
import copy
import fcntl
import json
import os
from pathlib import Path
import tempfile
import threading

from evdev import ecodes as E

PROFILES = {'codex': 'Codex', 'wechat': '微信', 'other': '其他软件'}
BUTTONS = {
    'power': ('电源', E.KEY_POWER), 'voice': ('语音', E.KEY_F5),
    'up': ('上', E.KEY_UP), 'left': ('左', E.KEY_LEFT),
    'confirm': ('确认', E.KEY_ENTER), 'right': ('右', E.KEY_RIGHT),
    'down': ('下', E.KEY_DOWN), 'back': ('返回', E.KEY_BACK),
    'volume_up': ('音量＋', E.KEY_VOLUMEUP), 'home': ('主页', None),
    'volume_down': ('音量－', E.KEY_VOLUMEDOWN), 'menu': ('菜单', None),
    'tv': ('电视', None),
}
EXTRA_BUTTONS = {'home', 'menu', 'tv'}
MODIFIERS = {'Ctrl': E.KEY_LEFTCTRL, 'Alt': E.KEY_LEFTALT,
             'Shift': E.KEY_LEFTSHIFT, 'Super': E.KEY_LEFTMETA}
KEYS = {name: getattr(E, 'KEY_' + code) for name, code in {
    'Backspace': 'BACKSPACE', 'Enter': 'ENTER', 'Escape': 'ESC', 'Tab': 'TAB',
    'Space': 'SPACE', 'Delete': 'DELETE', 'Insert': 'INSERT', 'Up': 'UP',
    'Down': 'DOWN', 'Left': 'LEFT', 'Right': 'RIGHT', 'Home': 'HOME', 'End': 'END',
    'PageUp': 'PAGEUP', 'PageDown': 'PAGEDOWN', 'Back': 'BACK',
    'VolumeUp': 'VOLUMEUP', 'VolumeDown': 'VOLUMEDOWN', 'Mute': 'MUTE',
    'PlayPause': 'PLAYPAUSE', 'NextSong': 'NEXTSONG', 'PreviousSong': 'PREVIOUSSONG',
}.items()}
KEYS.update({c: getattr(E, 'KEY_' + c) for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'})
# F5 与右 Alt 保留给已有听写流程，普通按键不能绕过播放保护触发它们。
KEYS.update({f'F{i}': getattr(E, f'KEY_F{i}') for i in range(1, 13) if i != 5})
OUTPUT_CODES = sorted(set(KEYS.values()) | set(MODIFIERS.values()))
NAVIGATION_DEFAULTS = {'enabled': False, 'pointer_speed': 3, 'scroll_speed': 3}


def navigation_options(data):
    return {**NAVIGATION_DEFAULTS, **data.get('navigation', {})}


def keymap_path():
    override = os.environ.get('XIAOMI_KEYMAP_CONFIG')
    return Path(override).expanduser() if override else Path(
        os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'xiaomi-codex-remote/keymap.json'


def defaults():
    return {'version': 1, 'profiles': {p: {} for p in PROFILES}, 'extra_buttons': {}}


def shortcut_codes(text):
    if not isinstance(text, str) or len(text) > 80:
        raise ValueError('快捷键格式不正确')
    if text == 'Super':
        return (E.KEY_LEFTMETA,)
    parts = text.split('+')
    if not parts or parts[-1] not in KEYS or len(parts) > 5:
        raise ValueError('请输入支持的快捷键，如 Ctrl+Z、Ctrl+Shift+Enter 或 Backspace；F5 保留给语音键')
    mods = parts[:-1]
    if len(set(mods)) != len(mods) or any(x not in MODIFIERS for x in mods):
        raise ValueError('组合键修饰键可用 Ctrl、Alt、Shift、Super，且不能重复')
    return tuple(MODIFIERS[x] for x in mods) + (KEYS[parts[-1]],)


def validate(data):
    required = {'version', 'profiles', 'extra_buttons'}
    if not isinstance(data, dict) or not required <= set(data) or set(data) - required - {'navigation'}:
        raise ValueError('按键配置结构不正确')
    if type(data['version']) is not int or data['version'] != 1:
        raise ValueError('不支持这个按键配置版本')
    if not isinstance(data['profiles'], dict) or set(data['profiles']) != set(PROFILES):
        raise ValueError('需要 Codex、微信和其他软件三组配置')
    for entries in data['profiles'].values():
        if not isinstance(entries, dict) or set(entries) - (set(BUTTONS) - {'voice'}):
            raise ValueError('存在不支持的按键；语音键由专用流程管理')
        for action in entries.values():
            if action not in ('default', 'disabled'):
                shortcut_codes(action)
    extra = data['extra_buttons']
    reserved = {code for _, code in BUTTONS.values() if code is not None} | {173, 191}
    if not isinstance(extra, dict) or set(extra) - EXTRA_BUTTONS:
        raise ValueError('只能检测绑定主页、菜单和电视键')
    if any(type(c) is not int or not 1 <= c <= E.KEY_MAX or c in reserved for c in extra.values()):
        raise ValueError('检测到的按键码无效，或已属于其他按键')
    if len(set(extra.values())) != len(extra):
        raise ValueError('同一个物理按键不能同时绑定两个名称')
    nav = data.get('navigation', {})
    if not isinstance(nav, dict) or set(nav) - set(NAVIGATION_DEFAULTS):
        raise ValueError('鼠标操作配置不正确')
    options = navigation_options(data)
    if type(options['enabled']) is not bool or any(
            type(options[k]) is not int or not 1 <= options[k] <= 5
            for k in ('pointer_speed', 'scroll_speed')):
        raise ValueError('鼠标操作开关须为布尔值，速度须为 1 到 5')
    return copy.deepcopy(data)


def read_bytes(path):
    if path.is_symlink():
        raise ValueError('按键配置不能使用符号链接')
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    if len(raw) > 65536:
        raise ValueError('按键配置超过大小限制')
    return raw


def decode(raw):
    try:
        return defaults() if raw is None else validate(json.loads(raw))
    except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        raise ValueError('按键配置无法解析，请从备份恢复或使用默认配置') from exc


def pending_bindings(data):
    """旧配置可以读取，但未识别实体键的动作不能被当作可用配置保存。"""
    pending = {button for entries in data['profiles'].values()
                   for button, action in entries.items()
                   if button in EXTRA_BUTTONS and action != 'default'
                   and button not in data['extra_buttons']}
    if navigation_options(data)['enabled'] and 'home' not in data['extra_buttons']:
        pending.add('home')
    return sorted(pending)


def bind_button(data, button, code):
    if button not in EXTRA_BUTTONS:
        raise ValueError('这个按键已有固定识别码，不需要重新识别')
    result = copy.deepcopy(data)
    result['extra_buttons'][button] = code
    return validate(result)


def atomic_write(path, raw):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save(data, expected, path=None):
    """保存时检查外部修改；上一版备份只包含按键映射。"""
    path = Path(path or keymap_path())
    value = validate(data)
    pending = pending_bindings(value)
    if pending:
        raise ValueError('请先识别实体按键：' + '、'.join(BUTTONS[b][0] for b in pending))
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = read_bytes(path)
        if current != expected:
            raise ValueError('配置已在另一处修改，请重新载入后再保存')
        previous = decode(current)
        atomic_write(path.with_suffix('.previous.json'),
                     (json.dumps(previous, ensure_ascii=False, indent=2) + '\n').encode())
        atomic_write(path, raw)
    return raw


def recover_defaults(expected, path=None):
    """显式恢复损坏配置；先保存原始字节，绝不覆盖有效或并发修改的文件。"""
    path = Path(path or keymap_path())
    fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = read_bytes(path)
        if current is None or current != expected:
            raise ValueError('配置已在另一处修改，请重新载入后再恢复')
        try:
            decode(current)
        except ValueError:
            pass
        else:
            raise ValueError('当前配置有效，不需要修复')
        fd, name = tempfile.mkstemp(prefix='keymap.invalid-', suffix='.json', dir=path.parent)
        with os.fdopen(fd, 'wb') as backup:
            backup.write(current)
            backup.flush()
            os.fsync(backup.fileno())
        raw = (json.dumps(defaults(), ensure_ascii=False, indent=2) + '\n').encode()
        atomic_write(path, raw)
    return raw, Path(name)


class MappingStore:
    def __init__(self, path=None, log=print):
        self.path = Path(path or keymap_path())
        self.log = log
        self.data = defaults()
        self.raw = None
        self.error = None
        self.revision = 0
        self.lock = threading.RLock()

    def refresh(self):
        with self.lock:
            try:
                raw = read_bytes(self.path)
                if raw != self.raw:
                    data = decode(raw)
                    self.data, self.raw = data, raw
                    self.revision += 1
                self.error = None
            except (OSError, ValueError) as exc:
                if str(exc) != self.error:
                    self.log(f'按键配置未加载，继续使用上次有效设置：{exc}')
                self.error = str(exc)
            return self.revision

    def button(self, code):
        for name, (_, original) in BUTTONS.items():
            if code == self.data['extra_buttons'].get(name, original):
                return name
        return None

    def action(self, button, context):
        context = context if context in PROFILES else 'other'
        return self.data['profiles'][context].get(button, 'default')

    def codes(self, button, context, original=None):
        action = self.action(button, context)
        if action == 'disabled':
            return ()
        if action != 'default':
            return shortcut_codes(action)
        if button == 'confirm':
            return (E.KEY_ENTER,) if context in {'codex', 'wechat'} else ()
        if button == 'power':
            return ()  # 沿用 SayAll 的电源键拦截行为。
        if button == 'back' and context in {'codex', 'wechat'}:
            return (E.KEY_BACKSPACE,)
        return (original,) if original is not None else ()
