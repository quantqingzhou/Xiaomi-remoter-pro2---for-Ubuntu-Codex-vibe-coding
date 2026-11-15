# SPDX-License-Identifier: GPL-3.0-only
"""按键界面的说明模型；只读取配置和状态，绝不触发输入或切换运行模式。"""
from dataclasses import dataclass
from keymap_config import EXTRA_BUTTONS, PROFILES, navigation_options

MODES = {'normal': '普通模式', 'mouse': '鼠标模式'}
SHORT_NAMES = {'Backspace': '退格', 'Enter': '发送', 'Escape': 'Esc / 取消',
    'Up': '↑', 'Down': '↓', 'Left': '←', 'Right': '→', 'PageUp': '向上翻页',
    'PageDown': '向下翻页', 'Delete': '删除', 'Home': '行首', 'End': '行尾',
    'VolumeUp': '音量＋', 'VolumeDown': '音量－', 'Mute': '静音', 'PlayPause': '播放 / 暂停',
    'Back': '返回', 'Space': '空格', 'Tab': 'Tab', 'Ctrl+Z': '撤销', 'Ctrl+C': '复制',
    'Ctrl+V': '粘贴', 'Ctrl+A': '全选'}


@dataclass(frozen=True)
class ButtonView:
    short: str
    title: str
    detail: str
    editable: bool = True
    fixed: bool = False
    edit_label: str = '按下时执行'


def button_view(data, profile, mode, button):
    supported = profile in ('codex', 'wechat')
    mouse = mode == 'mouse' and supported
    if mouse:
        direction = {'up': '向上', 'down': '向下', 'left': '向左', 'right': '向右'}
        if button in direction:
            text = direction[button]
            return ButtonView(text + '移动', '移动鼠标 · ' + text,
                '短按微调，按住逐渐加速；松手停止。移动速度可在下方调整。', False, True)
        if button == 'confirm':
            return ButtonView('单击 / 右键', '左键点击 / 右键菜单',
                '短按并松开：左键点击。长按约 0.6 秒：右键菜单，不会额外点击或发送消息。', False, True)
        if button in ('volume_up', 'volume_down'):
            text = '向上' if button == 'volume_up' else '向下'
            return ButtonView(text + '滚动', text + '滚动',
                '滚动鼠标光标所在区域，长按连续滚动。此模式下不调节系统音量。', False, True)
        if button == 'back' and profile == 'codex':
            return ButtonView('Esc / 取消', '取消 / Esc',
                '关闭菜单或取消当前操作；长按主页键退出鼠标模式。', False, True)
        if button == 'voice':
            return ButtonView('退出后听写', '先退出鼠标模式，再听写',
                '点击输入框，长按主页键退出鼠标模式，再按住语音键说话。此模式按语音键只显示提示。', False, True)
    if button == 'voice':
        text = '按住听写' if supported else '按住收音'
        detail = ('按住说话，松开结束 Codex 听写。' if profile == 'codex' else
                  '微信扩展启用后，按住说话，松开结束听写。' if profile == 'wechat' else
                  '使用遥控器麦克风，不触发听写快捷键。')
        return ButtonView(text, text, detail, False)
    action = data['profiles'][profile].get(button, 'default')
    if action == 'default':
        action = ('Backspace' if button == 'back' and supported else
                  'Enter' if button == 'confirm' and supported else
                  'disabled' if button in ('power', 'confirm') else
                  {'up': 'Up', 'down': 'Down', 'left': 'Left', 'right': 'Right',
                   'volume_up': 'VolumeUp', 'volume_down': 'VolumeDown', 'back': 'Back'}.get(button, 'original'))
    short = SHORT_NAMES.get(action, action.replace('+', ' + '))
    if action == 'disabled':
        short, title, detail = '无动作', '不执行动作', '此按键不会发送键盘快捷键。'
    elif not action:
        short, title, detail = '待设置', '等待录入快捷键', '在下方输入快捷键，或使用电脑键盘录入。'
    elif action == 'original':
        short, title, detail = '原始按键', '保留原始按键', '具体效果由前台软件决定，可以在下方指定快捷键。'
    else:
        title = short if short.replace(' ', '') == action else short + ' · ' + action
        detail = ('输入框获得焦点后发送；使用微信时，请与微信的发送快捷键保持一致。' if action in ('Enter', 'Ctrl+Enter') else
                  '点到文字输入框后使用；长按连续删除，松手停止。' if action in ('Backspace', 'Delete') else
                  '在该应用前台时执行此快捷键。')
    edit_label = '按下时执行（两种模式共用）' if mouse else '按下时执行'
    if mouse:
        detail += ' 此键沿用普通模式的设置。'
    if button == 'home' and supported and (mouse or navigation_options(data)['enabled']):
        destination = '退出鼠标模式' if mouse else '进入鼠标模式'
        title = '短按：' + short + '\n长按：' + destination
        short += '\n长按' + ('退出' if mouse else '切换')
        detail = '长按约 0.6 秒切换模式，不同时触发短按动作；切换前先松开其他按键。'
        edit_label = '短按时执行（两种模式共用）'
    if button in EXTRA_BUTTONS and button not in data['extra_buttons']:
        short = '待识别'
        detail = '先识别这个实体按键并保存，所选动作才可使用。' + detail
    return ButtonView(short, title, detail, edit_label=edit_label)


def runtime_mode_status(health, navigation, now, paused=False):
    """运行状态只取后台证据，不使用界面正在预览的模式或尚未保存的配置。"""
    stamp = health.get('time', 0)
    live = type(stamp) in (int, float) and 0 <= now - stamp < 3
    if not live:
        return ('paused', '当前：后台已暂停') if paused else ('unknown', '当前：运行状态待确认')
    if not navigation:
        return 'normal', '当前：普通模式'
    stamp = navigation.get('time', 0)
    if type(stamp) not in (int, float) or stamp > now or stamp <= 0:
        return 'unknown', '当前：模式状态待确认'
    if navigation.get('active') is False:
        # 普通模式没有常驻提示进程；退出记录可早于这次新鲜桥接心跳。
        return 'normal', '当前：普通模式'
    if navigation.get('active') is True and 0 <= now - stamp < 2:
        context = navigation.get('context')
        if context in ('codex', 'wechat'):
            return 'mouse', '当前：鼠标模式 · ' + PROFILES[context]
    return 'unknown', '当前：模式状态待确认'
