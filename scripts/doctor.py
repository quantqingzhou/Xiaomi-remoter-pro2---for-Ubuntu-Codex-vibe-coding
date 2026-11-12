#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""只读诊断：不注入按键、不录音，也不申请共享桌面。"""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from remote_config import load_config
from playback_protection import is_protected


def main():
    failures = []
    def check(label, okay, detail=''):
        print(f'[{"通过" if okay else "待检查"}] {label}' + (': ' + detail if detail else ''))
        if not okay:
            failures.append(label)
    def run(*args):
        return subprocess.run(args, text=True, capture_output=True, timeout=8)
    check('Python >= 3.10', sys.version_info >= (3, 10))
    for name in ['evdev', 'bleak']:
        check('Python 模块 ' + name, importlib.util.find_spec(name) is not None)
    for command in ['bluetoothctl', 'pw-cat', 'pw-loopback', 'pw-dump', 'wpctl', 'pactl', 'xprop', 'systemctl']:
        check(command, shutil.which(command) is not None)
    check('用于 X11/XWayland 焦点检测的 DISPLAY', bool(os.environ.get('DISPLAY')))
    check('uinput 访问权限', os.access('/dev/uinput', os.R_OK | os.W_OK),
          '需要 uinput 模块及当前桌面用户的访问权限')
    try:
        config = load_config()
        check('配置文件', True)
        if config.wechat_wm_classes:
            check('微信扩展：Fcitx5', shutil.which('fcitx5') is not None)
            check('微信扩展：Vinput 工具', any(shutil.which(name) for name in ['vinput', 'vinput-gui']))
            print('微信扩展已选择：请另外确认微信使用 Fcitx5、Alt_R 为按住说话，且识别服务与 SayAll Mic 已配置；程序存在不代表实际转写通过。')
        check('SayAll 桥接脚本', Path(config.sayall_bridge).is_file(), config.sayall_bridge)
        check('客户端播放保护', is_protected(Path(config.dictation_helper)),
              '保护失效时遥控器不会触发 F5；客户端更新后需要重新检查')
        if shutil.which('bluetoothctl'):
            info = run('bluetoothctl', 'info', config.remote_address)
            check('蓝牙连接状态', 'Connected: yes' in info.stdout)
        if importlib.util.find_spec('evdev'):
            import evdev
            readable = False
            for path in evdev.list_devices():
                device = None
                try:
                    device = evdev.InputDevice(path)
                    readable |= (device.uniq.lower() == config.remote_address
                                 and device.info.vendor == config.vendor_id
                                 and device.info.product == config.product_id
                                 and evdev.ecodes.KEY_F5 in device.capabilities().get(evdev.ecodes.EV_KEY, []))
                except (OSError, SystemError):
                    pass
                finally:
                    if device is not None:
                        device.close()
            check('所选遥控器的输入访问权限', readable)
        if shutil.which('pactl'):
            check('SayAll Mic 输入设备', 'sayall-mic\t' in run('pactl', 'list', 'short', 'sources').stdout)
            print('默认输出：', run('pactl', 'get-default-sink').stdout.strip())
        if shutil.which('systemctl'):
            for name in ['sayall-bridge.service', 'xiaomi-codex-keys.service']:
                result = run('systemctl', '--user', 'is-active', name)
                check(name, result.returncode == 0, result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        check('运行状态检查', False, str(exc))
    print('未修改任何设置。' + ('请检查以上标为“待检查”的项目。' if failures else '基础检查通过，请继续测试实际听写。'))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
