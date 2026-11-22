#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""小米蓝牙遥控器 2 Pro 的中文按键设置窗口。"""
import copy
import hashlib
from pathlib import Path
import re
import sys
import threading
import time

from keymap_config import (BUTTONS, EXTRA_BUTTONS, MappingStore, decode,
                           defaults, keymap_path, read_bytes, save, shortcut_codes, validate,
                           pending_bindings, bind_button, recover_defaults, navigation_options)
from key_detection import read_state, write_state
from key_presentation import MODES, VIEW_PROFILES as PROFILES, button_view, runtime_mode_status
from product_photo import cached_photo, fetch_photo
import settings_status
import service_controls

PRESETS = [
    ('default', '沿用默认'), ('disabled', '禁用'), ('Backspace', '退格'),
    ('Enter', '回车 / 发送'), ('Ctrl+Enter', 'Ctrl + 回车'), ('Escape', 'Esc / 取消'),
    ('Ctrl+Z', '撤销（Ctrl + Z）'), ('Ctrl+Shift+Z', '重做（Ctrl + Shift + Z）'),
    ('Ctrl+C', '复制'), ('Ctrl+V', '粘贴'), ('Ctrl+A', '全选'),
    ('Up', '方向：上'), ('Down', '方向：下'), ('Left', '方向：左'), ('Right', '方向：右'),
    ('PageUp', '向上翻页'), ('PageDown', '向下翻页'), ('Home', '行首'), ('End', '行尾'),
    ('VolumeUp', '音量增大'), ('VolumeDown', '音量减小'), ('Mute', '静音切换'),
    ('PlayPause', '播放 / 暂停'), ('Super', 'Super（打开活动概览）'), ('custom', '自定义组合键…'),
]


def app_version(directory=None):
    """安装目录和源码目录共用 VERSION；缺失时不编造版本号。"""
    directory = Path(directory) if directory is not None else Path(__file__).resolve().parent
    for path in (directory / 'VERSION', directory.parent / 'VERSION'):
        try:
            version = path.read_text().strip()
            if re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
                return version
        except OSError:
            pass
    return '版本未知'


def mapping_status(raw, health, now):
    """只报告桥接是否读到保存内容，不把写入磁盘当作运行时确认。"""
    stamp = health.get('time', 0)
    if type(stamp) not in (int, float) or not 0 <= now - stamp < 3:
        return 'offline', '桥接未连接，保存的配置尚未确认生效'
    if health.get('error'):
        return 'error', '配置读取异常，请查看状态与日志'
    if pending_bindings(decode(raw)):
        return 'unbound', '有按键待识别'
    if health.get('sha256') != hashlib.sha256(raw or b'').hexdigest():
        return 'pending', '已保存，等待桥接读取配置'
    return 'ready', ''


def recorded_shortcut(name, modifiers):
    modifiers = list(modifiers)
    if name == 'ISO_Left_Tab' and 'Shift' not in modifiers:
        modifiers.append('Shift')
    name = {'Return': 'Enter', 'KP_Enter': 'Enter', 'BackSpace': 'Backspace',
            'space': 'Space', 'Prior': 'PageUp', 'Page_Up': 'PageUp',
            'Next': 'PageDown', 'Page_Down': 'PageDown', 'ISO_Left_Tab': 'Tab'}.get(name, name)
    if name and len(name) == 1:
        name = name.upper()
    result = '+'.join(modifiers + [name or ''])
    shortcut_codes(result)
    return result



STYLE = """
window.remote-settings { background: #f4f6f8; color: #1c2936; border-radius: 14px; }
.settings-shell { border-radius: 0 0 14px 14px; }
.remote-settings headerbar { background: #f4f6f8; color: #566474; border: 0; box-shadow: none; min-height: 34px; }
.close-button { background: transparent; color: #415367; border: 0; border-radius: 18px; padding: 7px; }
.close-button label { font-size: 23px; font-weight: 400; }
.close-button:hover { background: #fbecec; color: #a33434; border-color: #edc5c5; }
.settings-page { padding: 12px 28px; }
.page-title { font-size: 27px; font-weight: 700; letter-spacing: 0.3px; }
.subtitle { color: #788493; font-size: 13px; }
.status-badge { background: #e8f2ed; color: #277250; border: 0; border-radius: 20px; padding: 8px 14px; font-size: 12px; }
.remote-settings separator { background: #e6ebf0; min-height: 1px; }
.remote-settings switch:checked { background: #286d8e; border-color: #286d8e; }
.status-badge.warning { background: #fff2dc; color: #94631c; }
.status-badge.unknown { background: #e8edf3; color: #677a8e; }
.status-value { color: #277250; font-size: 12px; }
.status-value.warning { color: #9a6419; }
.status-value.unknown { color: #788493; }
.status-panel { padding: 18px 24px; }
.log-view { background: #f6f8fa; color: #344b60; padding: 12px; font-size: 12px; border-radius: 10px; }
.eyebrow { color: #788493; font-size: 12px; font-weight: 600; }
.segmented { background: #e7ebf0; border-radius: 12px; padding: 4px; }
.remote-settings button { box-shadow: none; text-shadow: none; }
.profile-tab { background: transparent; border: 0; color: #6e7c8c; padding: 7px 8px; border-radius: 9px; }
.profile-tab:hover { background: #f0f3f6; }
.profile-tab.active-profile { background: #ffffff; color: #1f3549; box-shadow: 0 1px 4px alpha(#24344d, 0.08); font-weight: 600; }
.mode-tabs { background: #eef2f5; border-radius: 10px; padding: 3px; }
.mode-tab { background: transparent; border: 0; color: #65788a; padding: 6px 10px; border-radius: 8px; font-size: 13px; }
.mode-tab.active-mode { background: #286d8e; color: #ffffff; font-weight: 600; }
.mode-tab:disabled { color: #9ca9b5; }
.runtime-mode { color: #5b7181; background: #e9eef3; border-radius: 16px; padding: 6px 12px; font-size: 12px; }
.runtime-mode.mouse { background: #e4eff7; color: #21628a; }
.runtime-mode.warning { background: #fff2dc; color: #94631c; }
.mode-note { color: #6b7e8d; font-size: 12px; }
.function-title { color: #244b65; font-size: 19px; font-weight: 600; }
.function-detail { color: #697d8e; font-size: 12px; }
.function-badge { color: #3b7696; font-size: 11px; background: #edf4f8; border-radius: 7px; padding: 4px 8px; }
.key-callout { background: #f4f7f9; color: #5d7385; border: 1px solid #e4ebef; border-radius: 6px; padding: 3px 2px; min-width: 0; min-height: 0; font-size: 10px; }
.key-callout:hover { background: #e9f2f8; border-color: #94b8cf; }
.key-callout.mouse-action { color: #2a6b90; background: #edf5fa; }
.key-callout.selected-callout { background: #286d8e; border-color: #286d8e; color: #ffffff; }
.fallback-key { font-size: 12px; padding: 7px 9px; }
.fallback-key.selected-callout { background: #e4eff7; color: #21628a; border-color: #7ba8c2; }
.muted { color: #7b8795; font-size: 12px; }
.device-card { background: #ffffff; border: 1px solid #e6ebf0; border-radius: 20px; padding: 16px 22px; }
.card-caption { color: #69798b; font-size: 12px; }
.remote-key { min-width: 0; min-height: 0; padding: 0; margin: 0; background: transparent; border: 2px solid transparent; border-radius: 40px; box-shadow: none; }
.remote-key:hover { background: alpha(#75b4df, 0.17); border-color: #7bb3d6; }
.remote-key.selected-key { background: alpha(#4b9dcd, 0.16); border-color: #57a2ce; box-shadow: 0 0 0 3px alpha(#438ebc, 0.17); }
.photo-credit { color: #9aa6b2; font-size: 10px; }
.device-caption { color: #344b60; font-size: 13px; font-weight: 600; }
.editor-card { background: #ffffff; border: 1px solid #e6ebf0; border-radius: 20px; padding: 24px; }
.key-title { font-size: 25px; font-weight: 650; }
.binding-badge { color: #557768; font-size: 11px; background: #edf5f0; border-radius: 8px; padding: 5px 9px; }
.binding-badge.needs-binding { color: #9e681a; background: #fff3dd; }
.binding-notice { padding: 13px; background: #fff7e8; color: #8e6728; border-radius: 12px; font-size: 12px; }
.binding-notice button { background: #fffdf8; color: #906526; border: 1px solid #e5cfaa; border-radius: 8px; padding: 8px 12px; }
.binding-notice.binding-complete { background: #f5f8fa; color: #788493; }
.binding-notice.binding-complete button { background: transparent; color: #57738c; border-color: #dae3eb; }
.field-label { font-size: 13px; color: #53677c; font-weight: 600; }
.action-picker > button { background: #f6f8fa; color: #263b4e; border: 1px solid #dfe6ed; border-radius: 10px; min-height: 30px; padding: 6px 12px; }
.remote-settings popover { color: #273a4e; }
.remote-settings popover contents { background: #ffffff; border-color: #dce4ec; }
.remote-settings popover row:selected { background: #e9f1f7; color: #1b5178; }
/* GTK 下拉弹层有独立窗口，直接统一弹层节点，避免继承深色主题的浅色文字。 */
popover contents { background: #ffffff; color: #243a50; border: 1px solid #cdd8e2; border-radius: 12px; padding: 6px; }
popover label { color: #243a50; opacity: 1; }
popover listview { background: transparent; color: #243a50; }
popover row { background: transparent; color: #243a50; border-radius: 7px; min-height: 30px; padding: 4px 8px; }
popover row:hover { background: #eef3f7; }
popover row:selected { background: #e2edf5; color: #205d81; }
popover row:selected label { color: #205d81; }
popover entry { background: #ffffff; color: #243a50; border: 1px solid #d2dde7; border-radius: 8px; padding: 7px 10px; }
popover entry:focus-within { border-color: #83abc6; outline: 2px solid alpha(#4d8eb8, 0.22); outline-offset: -2px; box-shadow: none; }
popover separator { background: #e6ebf0; min-height: 1px; }
.remote-settings button:focus-visible { outline: 2px solid alpha(#4d8eb8, 0.45); outline-offset: -2px; }
.operations-popover contents { background: #ffffff; color: #243a50; border: 1px solid #cdd8e2; border-radius: 12px; padding: 6px; }
.operations-menu button { background: transparent; color: #243a50; border: 0; border-radius: 7px; padding: 10px 14px; min-height: 24px; }
.operations-menu button label { color: #243a50; opacity: 1; }
.operations-menu button:hover { background: #e8f0f7; }
.operations-menu button:focus-visible { outline: 2px solid #6498bb; outline-offset: -2px; }
.shortcut-entry { background: #ffffff; color: #243d53; border: 1px solid #b8ccdd; border-radius: 10px; min-height: 36px; padding: 6px 12px; font-size: 16px; }
.shortcut-preview { color: #2a4a65; font-size: 21px; font-weight: 600; padding: 10px 4px; }
.secondary-button { background: transparent; color: #52718b; border: 1px solid #cfdbe5; border-radius: 10px; padding: 9px 16px; }
.secondary-button:hover { background: #e5eef5; }
.secondary-button:checked { background: #e2edf6; color: #1e638f; border-color: #73a3c3; }
.runtime-controls { border-top: 1px solid #e6ebf0; padding-top: 12px; }
.editor-hint { color: #8995a3; font-size: 12px; padding-top: 8px; }
.summary-text { color: #7a8999; font-size: 12px; }
.settings-footer { background: #ffffff; border-top: 1px solid #e6ebf0; border-radius: 0 0 14px 14px; padding: 14px 28px; }
.settings-footer menubutton > button { background: transparent; color: #708196; border: 0; }
.status-text { color: #728194; font-size: 12px; }
.save-button { background: #286d8e; color: white; border: 1px solid #286d8e; border-radius: 11px; padding: 10px 25px; font-weight: 600; }
.save-button:hover { background: #215c79; }
.save-button:disabled { background: #e8eef2; color: #9aaab6; border-color: #e8eef2; }
"""


def main():
    try:
        import gi
        gi.require_version('Gtk', '4.0')
        gi.require_version('Graphene', '1.0')
        from gi.repository import Gtk, Gdk, Gio, GLib, Graphene, Pango
    except (ImportError, ValueError):
        print('设置窗口需要 GTK 4：sudo apt install python3-gi gir1.2-gtk-4.0', file=sys.stderr)
        return 1

    # 按原图坐标显示官网产品图；仅在窗口内裁掉留白，源文件保持原样。
    scale, origin_x, origin_y = 0.92, -233, -100

    class ProductPhoto(Gtk.Widget):
        def __init__(self):
            super().__init__()
            self.texture = None
            self.set_size_request(270, 536)
            self.set_overflow(Gtk.Overflow.HIDDEN)
            self.set_can_target(False)

        def do_snapshot(self, snapshot):
            if self.texture is None:
                return
            bounds = Graphene.Rect()
            bounds.init(origin_x, origin_y, 800 * scale, 800 * scale)
            snapshot.append_texture(self.texture, bounds)

    class StatusWindow(Gtk.Window):
        def __init__(self, parent):
            super().__init__(title='小米遥控器 · 状态与日志', transient_for=parent,
                             application=parent.get_application())
            self.owner = parent
            self.closed = False
            self.logs_busy = False
            self.set_destroy_with_parent(True)
            self.set_default_size(660, 780)
            self.add_css_class('remote-settings')
            header = Gtk.HeaderBar(show_title_buttons=False)
            header.set_title_widget(Gtk.Label(label='状态与日志'))
            self.close_button = Gtk.Button(label='×')
            self.close_button.add_css_class('close-button')
            self.close_button.set_tooltip_text('关闭状态与日志')
            self.close_button.update_property([Gtk.AccessibleProperty.LABEL], ['关闭状态与日志'])
            self.close_button.connect('clicked', lambda _b: self.close())
            header.pack_end(self.close_button)
            self.set_titlebar(header)
            panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            panel.add_css_class('status-panel')
            panel_scroll = Gtk.ScrolledWindow(vexpand=True)
            panel_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            panel_scroll.set_child(panel)
            self.set_child(panel_scroll)
            heading = Gtk.Box(spacing=10)
            title = Gtk.Label(label='设备状态', xalign=0, hexpand=True)
            title.add_css_class('key-title')
            heading.append(title)
            self.refresh_button = Gtk.Button(label='刷新')
            self.refresh_button.add_css_class('secondary-button')
            self.refresh_button.connect('clicked', lambda _b: self.refresh())
            heading.append(self.refresh_button)
            panel.append(heading)
            self.values = {}
            for key, name in settings_status.ROWS.items():
                row = Gtk.Box(spacing=12)
                label = Gtk.Label(label=name, xalign=0, hexpand=True)
                label.add_css_class('field-label')
                row.append(label)
                value = Gtk.Label(label='检查中…', xalign=1, wrap=True)
                value.set_max_width_chars(26)
                value.add_css_class('status-value')
                row.append(value)
                self.values[key] = value
                panel.append(row)
            self.updated = Gtk.Label(label='正在读取状态…', xalign=0)
            self.updated.add_css_class('muted')
            panel.append(self.updated)
            note = Gtk.Label(label='这里检查基础连接；收音效果请以实际波形和转写为准。', xalign=0, wrap=True)
            note.add_css_class('muted')
            panel.append(note)
            panel.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            log_heading = Gtk.Box(spacing=12)
            label = Gtk.Label(label='近期日志', xalign=0, hexpand=True)
            label.add_css_class('field-label')
            log_heading.append(label)
            self.log_filter = Gtk.DropDown.new_from_strings(['错误与警告', '全部日志'])
            self.log_filter.add_css_class('action-picker')
            self.log_filter.connect('notify::selected', lambda *_: self.refresh_logs())
            log_heading.append(self.log_filter)
            panel.append(log_heading)
            scroll = Gtk.ScrolledWindow(vexpand=True)
            scroll.set_min_content_height(180)
            self.log_view = Gtk.TextView(editable=False, cursor_visible=False,
                                         wrap_mode=Gtk.WrapMode.WORD_CHAR, monospace=True)
            self.log_view.add_css_class('log-view')
            self.log_view.get_buffer().set_text('正在读取日志…')
            scroll.set_child(self.log_view)
            panel.append(scroll)
            bottom = Gtk.Box(spacing=12)
            note = Gtk.Label(label='最近 24 小时，最多 200 条\n已隐藏蓝牙地址和用户目录', xalign=0, hexpand=True)
            note.add_css_class('muted')
            bottom.append(note)
            self.copy_button = Gtk.Button(label='复制日志')
            self.copy_button.add_css_class('secondary-button')
            self.copy_button.set_sensitive(False)
            self.copy_button.connect('clicked', self.copy_logs)
            bottom.append(self.copy_button)
            panel.append(bottom)
            self.connect('close-request', self.close_requested)
            if parent.status_snapshot:
                self.update_status(parent.status_snapshot)
            self.refresh()

        def refresh(self):
            self.owner.request_status()
            self.refresh_logs()

        def update_status(self, snapshot):
            for key, label in self.values.items():
                level, text = snapshot['items'].get(key, ('unknown', '状态未知'))
                label.set_text(('●  ' if level == 'ok' else '○  ') + text)
                for name in ('warning', 'unknown'):
                    (label.add_css_class if level == name else label.remove_css_class)(name)
            self.updated.set_text('更新于 ' + snapshot['time'] + ' · 窗口打开时自动检查')
        def refresh_logs(self):
            if self.logs_busy or self.closed:
                return
            self.logs_busy = True
            self.refresh_button.set_sensitive(False)
            self.log_filter.set_sensitive(False)
            errors_only = self.log_filter.get_selected() == 0
            def worker():
                try:
                    text = settings_status.read_logs(errors_only)
                except Exception:
                    text = '日志读取失败，请稍后重试。'
                GLib.idle_add(self.finish_logs, text)
            threading.Thread(target=worker, name='remote-logs', daemon=True).start()

        def finish_logs(self, text):
            self.logs_busy = False
            if not self.closed:
                self.log_view.get_buffer().set_text(text)
                self.refresh_button.set_sensitive(True)
                self.log_filter.set_sensitive(True)
                self.copy_button.set_sensitive(True)
            return False

        def copy_logs(self, *_):
            buffer = self.log_view.get_buffer()
            self.get_clipboard().set(buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False))

        def close_requested(self, *_):
            self.closed = True
            self.owner.status_window = None
            return False

    class SettingsWindow(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title='小米遥控器 · 按键设置')
            self.set_default_size(840, 940)
            self.add_css_class('remote-settings')
            self.path = keymap_path()
            self.original = None
            self.invalid_original = None
            self.load_error = None
            self.awaiting_saved = False
            self.draft = defaults()
            self.profile = 'codex'
            self.view_mode = 'normal'
            self.mode_buttons = {}
            self.callout_buttons = {}
            self.selected = 'back'
            self.loading = False
            self.recording = False
            self.detect_until = 0
            self.last_event = 0
            self.last_code = None
            self.learn_target = None
            self.dirty = False
            self.buttons = {}
            self.fallback_buttons = {}
            self.closed = False
            self.photo_pending = False
            self.status_window = None
            self.status_snapshot = None
            self.status_busy = False
            self.next_status_check = 0
            self.control_busy = False
            self.updating_controls = False
            self.profile_buttons = {}
            header = Gtk.HeaderBar()
            header.add_css_class('settings-header')
            header.set_title_widget(Gtk.Label(label='小米遥控器'))
            header.set_show_title_buttons(False)
            self.close_button = Gtk.Button(label='×')
            self.close_button.add_css_class('close-button')
            self.close_button.update_property([Gtk.AccessibleProperty.LABEL], ['关闭按键设置'])
            self.close_button.set_tooltip_text('关闭按键设置')
            self.close_button.connect('clicked', lambda _b: self.close())
            header.pack_end(self.close_button)
            self.set_titlebar(header)

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            root.add_css_class('settings-shell')
            root.set_overflow(Gtk.Overflow.HIDDEN)
            self.set_child(root)
            self.scroller = Gtk.ScrolledWindow(vexpand=True)
            self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            self.scroller.set_min_content_height(320)
            root.append(self.scroller)
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
            page.add_css_class('settings-page')
            self.scroller.set_child(page)
            heading_row = Gtk.Box(spacing=20)
            page.append(heading_row)
            titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
            title = Gtk.Label(label='遥控器设置', xalign=0)
            title.add_css_class('page-title')
            titles.append(title)
            subtitle = Gtk.Label(label='小米蓝牙遥控器 2 Pro  /  Linux', xalign=0)
            subtitle.add_css_class('subtitle')
            titles.append(subtitle)
            self.version_label = Gtk.Label(label='版本 ' + app_version(), xalign=0)
            self.version_label.add_css_class('eyebrow')
            titles.append(self.version_label)
            heading_row.append(titles)
            connection = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5,
                                 valign=Gtk.Align.CENTER, halign=Gtk.Align.END)
            device_badges = Gtk.Box(spacing=8, halign=Gtk.Align.END)
            self.bluetooth = Gtk.Button(label='蓝牙：检查中…')
            self.bluetooth.add_css_class('status-badge')
            self.bluetooth.add_css_class('unknown')
            self.bluetooth.set_tooltip_text('查看设备状态与日志')
            self.bluetooth.connect('clicked', self.open_status)
            device_badges.append(self.bluetooth)
            self.battery = Gtk.Label(label='电量：检查中…')
            self.battery.add_css_class('status-badge')
            self.battery.add_css_class('unknown')
            self.battery.set_tooltip_text('遥控器上报的剩余电量；读数可能延迟更新。')
            device_badges.append(self.battery)
            connection.append(device_badges)
            self.runtime_badge = Gtk.Label(label='当前：运行状态待确认')
            self.runtime_badge.add_css_class('runtime-mode')
            self.runtime_badge.set_tooltip_text('来自后台的实际运行状态；查看模式不会切换遥控器。鼠标模式在切换应用后保持，长按主页键退出。')
            connection.append(self.runtime_badge)
            self.health = Gtk.Label(xalign=0, wrap=True)
            self.health.set_max_width_chars(26)
            self.health.add_css_class('status-value')
            self.health.add_css_class('warning')
            self.health.set_visible(False)
            heading_row.append(connection)

            profile_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            caption = Gtk.Label(label='应用配置', xalign=0)
            caption.add_css_class('eyebrow')
            profile_row.append(caption)
            segmented = Gtk.Box(spacing=4)
            segmented.add_css_class('segmented')
            profile_row.append(segmented)
            self.profiles = Gtk.DropDown.new_from_strings(list(PROFILES.values()))
            self.profiles.connect('notify::selected', self.change_profile)
            for index, (key, label) in enumerate(PROFILES.items()):
                button = Gtk.Button(label=label, hexpand=True)
                button.add_css_class('profile-tab')
                button.connect('clicked', lambda _b, i=index: self.profiles.set_selected(i))
                segmented.append(button)
                self.profile_buttons[key] = button
            body = Gtk.Box(spacing=18, vexpand=True)
            page.append(body)
            device_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            device_card.add_css_class('device-card')
            device_card.set_size_request(350, -1)
            body.append(device_card)
            self.device_view_caption = Gtk.Label(label='普通模式按键', xalign=0)
            self.device_view_caption.add_css_class('card-caption')
            device_card.append(self.device_view_caption)
            stage = Gtk.Fixed(halign=Gtk.Align.CENTER)
            stage.set_size_request(270, 536)
            self.photo_stack = Gtk.Stack()
            device_card.append(self.photo_stack)
            self.photo_stack.add_named(stage, 'photo')
            self.product_photo = ProductPhoto()
            stage.put(self.product_photo, 0, 0)
            # 点击区对应 800×800 官方原图中的真实按键，不覆盖原图标。
            placements = {
                'power': (346, 136, 34, 36), 'voice': (420, 136, 35, 36),
                'up': (380, 184, 40, 26), 'down': (380, 278, 40, 26),
                'left': (341, 224, 27, 40), 'right': (432, 224, 27, 40),
                'confirm': (368, 211, 64, 66),
                'back': (345, 308, 50, 50), 'volume_up': (405, 308, 50, 50),
                'home': (345, 363, 50, 50), 'volume_down': (405, 363, 50, 50),
                'menu': (345, 418, 50, 50), 'tv': (405, 418, 50, 50),
            }
            for name, (x, y, width, height) in placements.items():
                button = Gtk.Button()
                button.set_tooltip_text(BUTTONS[name][0] + '键')
                button.update_property([Gtk.AccessibleProperty.LABEL], [BUTTONS[name][0] + '键'])
                button.set_size_request(round(width * scale), round(height * scale))
                button.add_css_class('remote-key')
                if name in ('up', 'down', 'left', 'right'):
                    button.add_css_class('direction-key')
                button.connect('clicked', lambda _b, n=name: self.select(n))
                stage.put(button, round(origin_x + x * scale), round(origin_y + y * scale))
                self.buttons[name] = button
            callouts = {'power': (0, 28), 'voice': (200, 28), 'up': (0, 72),
                'left': (0, 112), 'down': (0, 153), 'confirm': (200, 87),
                'right': (200, 133), 'back': (0, 195), 'volume_up': (200, 195),
                'home': (0, 246), 'volume_down': (200, 246), 'menu': (0, 297), 'tv': (200, 297)}
            for name, (x, y) in callouts.items():
                button = Gtk.Button()
                button.add_css_class('key-callout')
                button.set_size_request(70, -1)
                label = Gtk.Label()
                label.set_ellipsize(Pango.EllipsizeMode.END)
                label.set_max_width_chars(7)
                button.set_child(label)
                button.connect('clicked', lambda _b, n=name: self.select(n))
                stage.put(button, x, y)
                self.callout_buttons[name] = button
            fallback = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                               valign=Gtk.Align.CENTER)
            fallback.set_size_request(270, 536)
            self.photo_message = Gtk.Label(label='正在加载官方产品图…', wrap=True)
            self.photo_message.add_css_class('muted')
            self.photo_message.set_max_width_chars(24)
            fallback.append(self.photo_message)
            key_grid = Gtk.Grid(column_spacing=8, row_spacing=8, halign=Gtk.Align.CENTER)
            for index, name in enumerate(placements):
                button = Gtk.Button(label=BUTTONS[name][0] + '键')
                button.add_css_class('secondary-button')
                button.add_css_class('fallback-key')
                button.get_child().set_ellipsize(Pango.EllipsizeMode.END)
                button.get_child().set_max_width_chars(10)
                button.connect('clicked', lambda _b, n=name: self.select(n))
                key_grid.attach(button, index % 2, index // 2, 1, 1)
                self.fallback_buttons[name] = button
            fallback.append(key_grid)
            self.photo_retry = Gtk.Button(label='重新加载产品图')
            self.photo_retry.add_css_class('secondary-button')
            self.photo_retry.connect('clicked', lambda _b: self.load_photo())
            fallback.append(self.photo_retry)
            self.photo_stack.add_named(fallback, 'fallback')
            credit = Gtk.Label(label='产品图来源：小米官网')
            credit.add_css_class('photo-credit')
            device_card.append(credit)
            self.device_caption = Gtk.Label(label='返回键', halign=Gtk.Align.CENTER)
            self.device_caption.add_css_class('device-caption')
            # 当前键名已在右侧编辑器显示。
            self.detect = Gtk.ToggleButton(label='测试实体按键')
            self.detect.add_css_class('secondary-button')
            self.detect.connect('toggled', self.toggle_detection)
            device_card.append(self.detect)
            self.detect_label = Gtk.Label(label='点击图中的按键，或测试手中的遥控器。', wrap=True, xalign=0)
            self.detect_label.set_max_width_chars(28)
            self.detect_label.add_css_class('muted')
            device_card.append(self.detect_label)

            editor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True)
            editor.add_css_class('editor-card')
            body.append(editor)
            editor.append(profile_row)
            mode_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
            editor.append(mode_row)
            mode_label = Gtk.Label(label='查看按键模式', xalign=0)
            mode_label.add_css_class('eyebrow')
            mode_row.append(mode_label)
            mode_tabs = Gtk.Box(spacing=4)
            mode_tabs.add_css_class('mode-tabs')
            mode_row.append(mode_tabs)
            for mode, label in MODES.items():
                button = Gtk.Button(label=label, hexpand=True)
                button.add_css_class('mode-tab')
                button.connect('clicked', lambda _b, m=mode: self.change_view_mode(m))
                self.mode_buttons[mode] = button
                mode_tabs.append(button)
            self.mode_note = Gtk.Label(xalign=0, wrap=True)
            self.mode_note.set_max_width_chars(28)
            self.mode_note.add_css_class('mode-note')
            mode_row.append(self.mode_note)
            editor.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            breadcrumb = Gtk.Box(spacing=10)
            editor.append(breadcrumb)
            self.context_caption = Gtk.Label(xalign=0, hexpand=True)
            self.context_caption.add_css_class('eyebrow')
            # 应用与模式已由上方选项显示，键名和识别状态共用一行。
            self.heading = Gtk.Label(xalign=0, hexpand=True)
            self.heading.add_css_class('key-title')
            breadcrumb.append(self.heading)
            self.binding_badge = Gtk.Label()
            self.binding_badge.add_css_class('binding-badge')
            self.binding_badge.set_valign(Gtk.Align.CENTER)
            breadcrumb.append(self.binding_badge)
            function = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
            editor.append(function)
            self.current = Gtk.Label(xalign=0, wrap=True)
            self.current.set_max_width_chars(26)
            self.current.add_css_class('function-title')
            function.append(self.current)
            self.function_scope = Gtk.Label(xalign=0, halign=Gtk.Align.START)
            self.function_scope.add_css_class('function-badge')
            function.append(self.function_scope)
            self.function_detail = Gtk.Label(xalign=0, wrap=True)
            self.function_detail.set_max_width_chars(28)
            self.function_detail.add_css_class('function-detail')
            function.append(self.function_detail)
            self.shared_edit = Gtk.Button(label='编辑“其他软件”的共用设置', halign=Gtk.Align.START)
            self.shared_edit.add_css_class('secondary-button')
            self.shared_edit.connect('clicked', lambda _b: self.profiles.set_selected(list(PROFILES).index('other')))
            function.append(self.shared_edit)
            self.binding_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            self.binding_box.add_css_class('binding-notice')
            editor.append(self.binding_box)
            self.binding_message = Gtk.Label(label='先让电脑认识这个实体按键，再设置它的操作。', wrap=True, xalign=0)
            self.binding_message.set_max_width_chars(26)
            self.binding_box.append(self.binding_message)
            self.bind = Gtk.Button(label='识别这个按键')
            self.bind.connect('clicked', self.bind_detected)
            self.binding_box.append(self.bind)
            self.action_label = Gtk.Label(label='按下时执行', xalign=0)
            self.action_label.add_css_class('field-label')
            editor.append(self.action_label)
            self.actions = Gtk.DropDown.new_from_strings([label for _, label in PRESETS])
            self.actions.add_css_class('action-picker')
            self.actions.set_enable_search(True)
            self.actions.connect('notify::selected', self.change_action)
            editor.append(self.actions)
            self.entry = Gtk.Entry(placeholder_text='例如 Alt+A 或 Ctrl+Shift+Z')
            self.entry.add_css_class('shortcut-entry')
            self.entry.connect('changed', self.change_custom)
            editor.append(self.entry)
            self.shortcut_preview = Gtk.Label(xalign=0)
            self.shortcut_preview.add_css_class('shortcut-preview')
            editor.append(self.shortcut_preview)
            self.record = Gtk.ToggleButton(label='录入键盘快捷键')
            self.record.add_css_class('secondary-button')
            self.record.connect('toggled', self.toggle_record)
            editor.append(self.record)
            self.runtime_controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.runtime_controls.add_css_class('runtime-controls')
            editor.append(self.runtime_controls)
            controls = Gtk.Box(spacing=10)
            self.pause_button = Gtk.Button(label='暂停后台', hexpand=True)
            self.resume_button = Gtk.Button(label='恢复运行', hexpand=True)
            for button, action in ((self.pause_button, service_controls.pause),
                                   (self.resume_button, service_controls.resume)):
                button.add_css_class('secondary-button')
                button.connect('clicked', lambda _b, fn=action: self.run_control(fn))
                controls.append(button)
            self.runtime_controls.append(controls)
            self.runtime_controls.append(self.health)
            startup_row = Gtk.Box(spacing=10)
            start_label = Gtk.Label(label='登录后自动启动', hexpand=True, xalign=0)
            start_label.add_css_class('muted')
            startup_row.append(start_label)
            self.autostart = Gtk.Switch(valign=Gtk.Align.CENTER)
            self.autostart.set_sensitive(False)
            self.autostart.connect('notify::active', self.change_autostart)
            startup_row.append(self.autostart)
            self.runtime_controls.append(startup_row)
            self.control_message = Gtk.Label(label='暂停映射与麦克风；原始蓝牙键仍可能响应。× 只关闭窗口。', xalign=0, wrap=True)
            self.control_message.set_max_width_chars(26)
            self.control_message.add_css_class('muted')
            self.runtime_controls.append(self.control_message)
            self.navigation_expander = Gtk.Expander(label='鼠标操作 · 长按主页键切换')
            self.navigation_expander.add_css_class('field-label')
            self.runtime_controls.append(self.navigation_expander)
            mouse_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.navigation_expander.set_child(mouse_box)
            mouse_row = Gtk.Box(spacing=10)
            enabled_label = Gtk.Label(label='在所有应用中启用', xalign=0, hexpand=True)
            enabled_label.add_css_class('field-label')
            mouse_row.append(enabled_label)
            self.navigation_enabled = Gtk.Switch(valign=Gtk.Align.CENTER)
            self.navigation_enabled.connect('notify::active', self.change_navigation)
            mouse_row.append(self.navigation_enabled)
            mouse_box.append(mouse_row)
            self.navigation_speeds = {}
            for field, label in (('pointer_speed', '鼠标移动速度'), ('scroll_speed', '滚动速度')):
                row = Gtk.Box(spacing=10)
                speed_label = Gtk.Label(label=label, xalign=0, hexpand=True)
                speed_label.add_css_class('field-label')
                row.append(speed_label)
                picker = Gtk.DropDown.new_from_strings(['很慢', '较慢', '标准', '较快', '很快'])
                picker.add_css_class('action-picker')
                picker.connect('notify::selected', self.change_navigation)
                self.navigation_speeds[field] = picker
                row.append(picker)
                mouse_box.append(row)
            mouse_hint = Gtk.Label(label='长按主页全局切换，短按保留原动作。方向键移动，确认点击，菜单键右击（需先识别）；长按确认也可右击。音量键滚动。返回键：微信沿用设置，Claude 为退格，其他为 Esc。听写前先点击输入框并退出鼠标模式。', xalign=0, wrap=True)
            mouse_hint.set_max_width_chars(26)
            mouse_hint.add_css_class('muted')
            mouse_box.append(mouse_hint)
            self.hint = Gtk.Label(xalign=0, wrap=True, vexpand=True, valign=Gtk.Align.END)
            self.hint.set_max_width_chars(26)
            self.hint.add_css_class('editor-hint')
            # 焦点说明放入动作提示，避免重复段落挤走常用运行控制。

            self.summary = Gtk.Label(xalign=0, wrap=True)
            self.summary.add_css_class('summary-text')
            # 摘要保留用于状态与自动验证，避免界面出现重复说明。
            footer = Gtk.Box(spacing=16)
            footer.add_css_class('settings-footer')
            root.append(footer)
            more = Gtk.MenuButton(label='更多操作')
            popover = Gtk.Popover()
            popover.add_css_class('operations-popover')
            popover.set_position(Gtk.PositionType.TOP)
            more.set_direction(Gtk.ArrowType.UP)
            self.more_button, self.more_popover = more, popover
            menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            menu.add_css_class('operations-menu')
            menu.set_size_request(240, -1)
            for label, callback in [('重新载入已保存设置', self.reload), ('恢复本应用默认', self.reset_profile),
                                    ('撤销上次保存', self.undo), ('重新识别当前按键', self.bind_detected)]:
                button = Gtk.Button(label=label)
                button.get_child().set_xalign(0)
                button.add_css_class('flat')
                if label == '恢复本应用默认':
                    self.reset_profile_button = button
                button.connect('clicked', lambda _b, cb=callback: (popover.popdown(), cb()))
                menu.append(button)
            self.repair_button = Gtk.Button(label='备份损坏配置并恢复默认…')
            self.repair_button.get_child().set_xalign(0)
            self.repair_button.set_visible(False)
            self.repair_button.connect('clicked', lambda _b: (popover.popdown(), self.repair_config()))
            menu.append(self.repair_button)
            popover.set_child(menu)
            more.set_popover(popover)
            footer.append(more)
            self.diagnostics_button = Gtk.Button(label='状态与日志')
            self.diagnostics_button.add_css_class('secondary-button')
            self.diagnostics_button.connect('clicked', self.open_status)
            footer.append(self.diagnostics_button)
            self.status = Gtk.Label(xalign=0, wrap=True, hexpand=True)
            self.status.set_max_width_chars(45)
            self.status.add_css_class('status-text')
            footer.append(self.status)
            self.save_button = Gtk.Button(label='保存设置')
            self.save_button.add_css_class('save-button')
            self.save_button.connect('clicked', self.save_draft)
            footer.append(self.save_button)
            controller = Gtk.EventControllerKey()
            controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            controller.connect('key-pressed', self.capture_shortcut)
            self.add_controller(controller)
            self.connect('notify::is-active', self.active_changed)
            self.connect('close-request', self.close_requested)
            self.timer = GLib.timeout_add(250, self.tick)
            self.reload()
            self.load_photo()
            self.request_status()

        def change_autostart(self, *_):
            if self.updating_controls or self.control_busy:
                return
            active = self.autostart.get_active()
            self.run_control(lambda: service_controls.set_autostart(active))

        def refresh_navigation(self):
            previous = self.loading
            self.loading = True
            options = navigation_options(self.draft)
            self.navigation_enabled.set_active(options['enabled'])
            for field, picker in self.navigation_speeds.items():
                picker.set_selected(options[field] - 1)
            self.loading = previous

        def change_navigation(self, *_):
            if self.loading or not hasattr(self, 'navigation_speeds'):
                return
            self.draft['navigation'] = {'enabled': self.navigation_enabled.get_active(),
                **{field: int(picker.get_selected()) + 1 for field, picker in self.navigation_speeds.items()}}
            self.changed()
            if self.draft['navigation']['enabled'] and 'home' not in self.draft['extra_buttons']:
                self.message('启用鼠标操作前，请选择主页键并识别实体按键，再保存。')

        def run_control(self, action):
            if self.control_busy or self.closed:
                return
            self.detect.set_active(False)
            self.record.set_active(False)
            self.control_busy = True
            self.control_message.set_text('正在处理，请稍候…')
            for button in (self.pause_button, self.resume_button, self.autostart):
                button.set_sensitive(False)
            def worker():
                try:
                    message = action()
                except Exception as exc:
                    message = '操作未完成：' + settings_status.redact(str(exc))[:280]
                GLib.idle_add(self.finish_control, message)
            threading.Thread(target=worker, name='remote-control', daemon=True).start()

        def finish_control(self, message):
            self.control_busy = False
            if not self.closed:
                self.control_message.set_text(message)
                self.pause_button.set_sensitive(True)
                self.resume_button.set_sensitive(True)
                self.next_status_check = 0
                self.request_status()
            return False

        def open_status(self, *_):
            self.detect.set_active(False)
            self.record.set_active(False)
            if self.status_window is None:
                self.status_window = StatusWindow(self)
            self.status_window.present()

        def request_status(self):
            if self.closed or self.status_busy:
                return
            self.status_busy = True
            self.next_status_check = time.monotonic() + 8
            def worker():
                try:
                    snapshot = settings_status.collect_status()
                    snapshot['autostart'] = service_controls.enabled()
                except Exception:
                    snapshot = {'time': time.strftime('%H:%M:%S'), 'items': {
                        key: ('unknown', '查询失败，状态未知') for key in settings_status.ROWS}}
                GLib.idle_add(self.finish_status, snapshot)
            threading.Thread(target=worker, name='remote-status', daemon=True).start()

        def finish_status(self, snapshot):
            self.status_busy = False
            if not self.closed:
                self.status_snapshot = snapshot
                level, text = snapshot['items']['bluetooth']
                self.bluetooth.set_label('●  蓝牙：' + text if level == 'ok' else '○  蓝牙：' + text)
                for name in ('warning', 'unknown'):
                    (self.bluetooth.add_css_class if level == name else self.bluetooth.remove_css_class)(name)
                battery_level, battery_text = snapshot['items'].get('battery', ('unknown', '电量未知'))
                percentage = snapshot.get('battery_percent')
                valid_battery = type(percentage) is int and 0 <= percentage <= 100
                self.battery.set_label(f'电量：{percentage}%' if valid_battery else '电量：未知')
                if not valid_battery:
                    battery_level = 'unknown'
                self.battery.set_tooltip_text(battery_text + '。遥控器上报的读数可能延迟更新。')
                for name in ('warning', 'unknown'):
                    (self.battery.add_css_class if battery_level == name else self.battery.remove_css_class)(name)
                self.updating_controls = True
                autostart = snapshot.get('autostart')
                if type(autostart) is bool:
                    self.autostart.set_active(autostart)
                self.autostart.set_sensitive(type(autostart) is bool and not self.control_busy)
                self.updating_controls = False
                if self.status_window:
                    self.status_window.update_status(snapshot)
            return False

        def load_photo(self):
            if self.photo_pending or self.closed:
                return
            cached = cached_photo()
            if cached:
                self.finish_photo(cached, None)
                return
            self.photo_stack.set_visible_child_name('fallback')
            self.photo_message.set_text('正在加载官方产品图；也可以先从下方选择按键。')
            self.photo_retry.set_sensitive(False)
            self.photo_pending = True
            def worker():
                try:
                    path, error = fetch_photo(), None
                except Exception:
                    path, error = None, '产品图暂时无法加载。可以继续选择下方按键，稍后重试。'
                GLib.idle_add(self.finish_photo, path, error)
            threading.Thread(target=worker, name='product-photo', daemon=True).start()

        def finish_photo(self, path, error):
            self.photo_pending = False
            if self.closed:
                return False
            self.photo_retry.set_sensitive(True)
            if path:
                try:
                    self.product_photo.texture = Gdk.Texture.new_from_filename(str(path))
                    self.product_photo.queue_draw()
                    self.photo_stack.set_visible_child_name('photo')
                    return False
                except (GLib.Error, OSError):
                    error = '产品图无法显示。请从下方选择按键。'
            self.photo_message.set_text(error)
            self.photo_stack.set_visible_child_name('fallback')
            return False

        def message(self, text):
            self.status.set_text(text)

        def changed(self):
            self.dirty = True
            self.message('有未保存的修改')
            self.update_summary()

        def update_summary(self):
            items = self.draft['profiles']['other' if self.profile == 'claude' else self.profile]
            descriptions = [f'{BUTTONS[b][0]} → {a}' for b, a in items.items() if a != 'default']
            self.summary.set_text('  ·  '.join(descriptions) if descriptions else '当前应用使用默认按键配置')
            action = items.get(self.selected, 'default')
            labels = dict(PRESETS)
            self.shortcut_preview.set_text(labels.get(action, action.replace('+', '  +  ')))
            # 当前功能已在上方统一展示，不再重复显示第二份快捷键。
            self.shortcut_preview.set_visible(False)
            pending = pending_bindings(self.draft)
            self.save_button.set_sensitive(self.dirty and not pending and not self.detect_until and not self.load_error)
            self.save_button.set_tooltip_text('先识别：' + '、'.join(BUTTONS[b][0] for b in pending) if pending else '保存后自动生效')
            if pending:
                self.message('还差一步：先识别' + '、'.join(BUTTONS[b][0] + '键' for b in pending))
            self.refresh_presentation()

        def refresh_presentation(self):
            mouse = self.view_mode == 'mouse'
            self.device_view_caption.set_text(PROFILES[self.profile] + ' · ' + MODES[self.view_mode] + '按键')
            self.context_caption.set_text(PROFILES[self.profile] + '  /  ' + MODES[self.view_mode])
            for mode, button in self.mode_buttons.items():
                (button.add_css_class if mode == self.view_mode else button.remove_css_class)('active-mode')
                button.set_sensitive(True)
            if mouse and not navigation_options(self.draft)['enabled']:
                note = '正在查看鼠标按键。需在下方开启鼠标操作并保存。'
            elif mouse and 'home' not in self.draft['extra_buttons']:
                note = '先识别主页键，再保存，才能长按切换模式。'
            elif navigation_options(self.draft)['enabled']:
                note = '长按主页切换全局鼠标模式；这里仅查看按键。'
            else:
                note = '普通模式用于听写和文字编辑。'
            if self.profile == 'claude' and not mouse:
                note = '启用 Claude 接入后生效；语音、发送和退格为专用按键。'
            self.mode_note.set_text(note)
            for name in BUTTONS:
                view = button_view(self.draft, self.profile, self.view_mode, name)
                tooltip = BUTTONS[name][0] + '键 · ' + MODES[self.view_mode] + '\n' + view.title + '\n' + view.detail
                for button in (self.buttons[name], self.callout_buttons[name], self.fallback_buttons[name]):
                    button.set_tooltip_text(tooltip)
                    button.update_property([Gtk.AccessibleProperty.LABEL], [BUTTONS[name][0] + '键：' + view.title])
                callout = self.callout_buttons[name]
                callout.get_child().set_text(view.short)
                (callout.add_css_class if view.fixed else callout.remove_css_class)('mouse-action')
                (callout.add_css_class if name == self.selected else callout.remove_css_class)('selected-callout')
                self.fallback_buttons[name].set_label(BUTTONS[name][0] + '键\n' + view.short.replace('\n', ' / '))
                (self.fallback_buttons[name].add_css_class if name == self.selected else self.fallback_buttons[name].remove_css_class)('selected-callout')
            view = button_view(self.draft, self.profile, self.view_mode, self.selected)
            self.current.set_text(view.title)
            self.function_detail.set_text(view.detail)
            self.function_scope.set_text('固定操作' if view.fixed else
                '沿用其他软件配置' if self.profile == 'claude' else
                '与普通模式共用设置' if mouse and view.editable else
                '专用语音键' if not view.editable else '可自定义')
            self.action_label.set_text(view.edit_label)

        def change_view_mode(self, mode):
            if mode not in MODES:
                return
            self.view_mode = mode
            self.select(self.selected)

        def select(self, name):
            self.selected = name
            self.loading = True
            self.record.set_active(False)
            for key, button in self.buttons.items():
                (button.add_css_class if key == name else button.remove_css_class)('selected-key')
            for key, button in self.profile_buttons.items():
                (button.add_css_class if key == self.profile else button.remove_css_class)('active-profile')
            self.heading.set_text(BUTTONS[name][0] + '键')
            self.device_caption.set_text(BUTTONS[name][0] + '键')
            self.context_caption.set_text(PROFILES[self.profile] + '  /  按键功能')
            view = button_view(self.draft, self.profile, self.view_mode, name)
            self.shared_edit.set_visible(self.profile == 'claude' and not view.fixed)
            self.reset_profile_button.set_sensitive(self.profile != 'claude')
            learned = name not in EXTRA_BUTTONS or name in self.draft['extra_buttons']
            self.binding_badge.set_text('已识别' if learned else '待识别')
            (self.binding_badge.remove_css_class if learned else self.binding_badge.add_css_class)('needs-binding')
            self.actions.set_visible(view.editable)
            self.action_label.set_visible(view.editable)
            self.record.set_visible(view.editable)
            self.binding_box.set_visible(name in EXTRA_BUTTONS and not learned)
            self.bind.set_label('重新识别此键' if learned else '识别' + BUTTONS[name][0] + '键')
            self.binding_message.set_text('实体按键已对应，可以直接设置动作。' if learned else '这个实体按键还没有对应到电脑。点下方按钮，然后轻按遥控器上的' + BUTTONS[name][0] + '键。')
            (self.binding_box.add_css_class if learned else self.binding_box.remove_css_class)('binding-complete')
            action = self.draft['profiles']['other' if self.profile == 'claude' else self.profile].get(name, 'default')
            values = [key for key, _ in PRESETS]
            self.actions.set_selected(values.index(action) if action in values else len(values) - 1)
            self.entry.set_text(action if action not in ('default', 'disabled') else '')
            self.entry.set_visible(view.editable and action not in values)
            if name == 'voice':
                self.hint.set_text('语音键使用专用收音流程，暂不支持改为普通快捷键。')
            elif view.fixed:
                self.hint.set_text('这是已启用接入后的 Claude 专用操作。' if self.profile == 'claude' and self.view_mode == 'normal'
                    else '这是此模式的固定操作。普通模式的自定义设置仍会保留。')
            elif self.profile == 'claude':
                self.hint.set_text('此键与其他软件共用设置；点击上方入口编辑。')
            else:
                target = '仅在' + PROFILES[self.profile] + '位于前台时生效。' if self.profile != 'other' else '适用于 Codex、微信之外的窗口。'
                self.hint.set_text(target + ' 点击输入框后再编辑或发送文字。')
            self.current.set_tooltip_text(self.hint.get_text())
            self.loading = False
            self.update_summary()

        def change_profile(self, *_):
            self.profile = list(PROFILES)[self.profiles.get_selected()]
            self.select(self.selected)

        def change_action(self, *_):
            if self.loading or not button_view(self.draft, self.profile, self.view_mode, self.selected).editable:
                return
            action = PRESETS[self.actions.get_selected()][0]
            self.entry.set_visible(action == 'custom')
            if action == 'custom':
                self.entry.grab_focus()
                self.change_custom()
            else:
                self.draft['profiles'][self.profile][self.selected] = action
                self.changed()

        def change_custom(self, *_):
            if (self.loading or PRESETS[self.actions.get_selected()][0] != 'custom'
                    or not button_view(self.draft, self.profile, self.view_mode, self.selected).editable): return
            self.draft['profiles'][self.profile][self.selected] = self.entry.get_text().strip()
            self.changed()

        def toggle_record(self, button):
            self.recording = button.get_active()
            button.set_label('现在按下组合键…' if self.recording else '录入键盘快捷键')
            if self.recording:
                self.message('请按电脑键盘上的组合键；Esc 取消。')

        def capture_shortcut(self, _controller, keyval, _code, state):
            if not self.recording: return False
            name = Gdk.keyval_name(keyval)
            if name == 'Escape':
                self.record.set_active(False)
                self.message('已取消录入。')
                return True
            if name in ('Control_L', 'Control_R', 'Alt_L', 'Alt_R', 'Shift_L', 'Shift_R', 'Super_L', 'Super_R'):
                return True
            mods = [text for mask, text in [(Gdk.ModifierType.CONTROL_MASK, 'Ctrl'),
                     (Gdk.ModifierType.ALT_MASK, 'Alt'), (Gdk.ModifierType.SHIFT_MASK, 'Shift'),
                     (Gdk.ModifierType.SUPER_MASK, 'Super')] if state & mask]
            try:
                shortcut = recorded_shortcut(name, mods)
                self.actions.set_selected(len(PRESETS) - 1)
                self.entry.set_text(shortcut)
                self.record.set_active(False)
            except ValueError as exc:
                self.message(str(exc))
            return True

        def reload(self, *_):
            raw = None
            try:
                raw = read_bytes(self.path)
                data = decode(raw)
                self.original, self.draft = raw, data
                self.load_error = self.invalid_original = None
                self.repair_button.set_visible(False)
                self.awaiting_saved = False
                self.dirty = False
                self.select(self.selected)
                self.refresh_navigation()
                self.message('已载入保存的设置')
                self.update_summary()
            except (OSError, ValueError) as exc:
                self.load_error = str(exc)
                self.invalid_original = raw
                self.repair_button.set_visible(raw is not None)
                self.select(self.selected)
                self.message(f'无法载入：{exc}')

        def repair_config(self):
            if self.invalid_original is None:
                return
            expected = self.invalid_original
            dialog = Gtk.MessageDialog(transient_for=self, modal=True,
                text='恢复默认按键配置？', secondary_text='先将损坏文件另存备份，再恢复三组默认映射。已有的有效备份不会被覆盖。')
            dialog.add_css_class('remote-settings')
            dialog.add_button('取消', Gtk.ResponseType.CANCEL).add_css_class('secondary-button')
            dialog.add_button('备份并恢复', Gtk.ResponseType.OK).add_css_class('secondary-button')
            def response(d, result):
                d.destroy()
                if result == Gtk.ResponseType.OK and not self.closed:
                    try:
                        _raw, backup = recover_defaults(expected, self.path)
                        self.reload()
                        self.message('已恢复默认，损坏配置备份：' + backup.name)
                    except (OSError, ValueError) as exc:
                        self.message(f'未恢复：{exc}')
            dialog.connect('response', response)
            dialog.present()

        def reset_profile(self, *_):
            if self.profile == 'claude':
                self.message('Claude 专用键为固定操作；共用按键请到“其他软件”页编辑。')
                return
            self.draft['profiles'][self.profile] = {}
            self.select(self.selected)
            self.changed()

        def undo(self, *_):
            try:
                raw = read_bytes(self.path.with_suffix('.previous.json'))
                if raw is None: raise ValueError('还没有上一次保存的备份')
                self.draft = decode(raw)
                self.select(self.selected)
                self.refresh_navigation()
                self.changed()
                self.message('已取回上一次设置，点击保存完成撤销。')
                self.update_summary()
            except (OSError, ValueError) as exc:
                self.message(str(exc))

        def save_draft(self, *_):
            if self.load_error:
                self.message('请先在“更多操作”中重新载入或备份损坏配置并恢复默认')
                return
            try:
                self.original = save(self.draft, self.original, self.path)
                self.dirty = False
                self.awaiting_saved = True
                self.message('已保存，等待桥接读取')
                self.update_summary()
            except (OSError, ValueError) as exc:
                self.message(f'未保存：{exc}')

        def toggle_detection(self, button):
            self.detect_until = time.monotonic() + 30 if button.get_active() else 0
            self.last_code = None
            self.last_event = time.monotonic()
            if not button.get_active():
                self.learn_target = None
                self.bind.set_label('重新识别此键' if self.selected in self.draft['extra_buttons']
                                    else '识别' + BUTTONS[self.selected][0] + '键')
            try:
                write_state('detect.json', {'time': time.monotonic() if button.get_active() and self.is_active() else 0})
            except OSError as exc:
                self.message(f'无法开始检测：{exc}')
                self.detect_until = 0
            button.set_label('结束按键测试' if self.detect_until else '测试实体按键')
            self.detect_label.set_text('现在按遥控器上的键，只检测、不执行动作。' if self.detect_until else '点击示意图，或测试手中的遥控器。')
            self.update_summary()

        def active_changed(self, *_):
            if not self.is_active():
                self.detect.set_active(False)
                self.record.set_active(False)

        def bind_detected(self, *_):
            if self.selected not in EXTRA_BUTTONS:
                self.message('这个按键已固定识别，无需重新识别。')
                return
            target = self.selected
            self.detect.set_active(False)
            self.detect.set_active(True)
            self.learn_target = target
            self.message('请轻按并松开实体' + BUTTONS[target][0] + '键')
            self.detect_label.set_text('正在识别' + BUTTONS[target][0] + '键…')
            self.bind.set_label('等待实体按键…')

        def accept_detection(self, event):
            if type(event.get('code')) is not int:
                return
            self.last_event = event['time']
            self.last_code = event['code']
            if self.learn_target:
                target = self.learn_target
                try:
                    self.draft = bind_button(self.draft, target, self.last_code)
                except ValueError as exc:
                    self.message(str(exc) + '。请按' + BUTTONS[target][0] + '键。')
                    return
                self.detect.set_active(False)
                self.select(target)
                self.changed()
                self.message('已识别' + BUTTONS[target][0] + '键，请保存设置')
            else:
                store = MappingStore()
                store.data = self.draft
                button = store.button(self.last_code)
                if button:
                    self.select(button)
                    self.detect_label.set_text('收到：' + BUTTONS[button][0] + '键  ✓')
                else:
                    self.detect_label.set_text('收到一个未识别的按键。请选择对应按钮，再点击“识别”。')
                    self.message('按键信号已收到，还没有绑定名称')

        def tick(self):
            now = time.monotonic()
            if now >= self.next_status_check:
                self.request_status()
            health = read_state('bridge.json')
            navigation = read_state('navigation.json')
            # 文件可能恰好在读取期间被后台更新，读取后取时间以免误报来自“未来”。
            now = time.monotonic()
            mode, label = runtime_mode_status(health, navigation, now,
                                              service_controls.read_preferences().get('paused') is True)
            if self.runtime_badge.get_text() != label:
                self.runtime_badge.set_text(label)
            for style, active in (('mouse', mode == 'mouse'), ('warning', mode in ('unknown', 'paused'))):
                (self.runtime_badge.add_css_class if active else self.runtime_badge.remove_css_class)(style)
            level, text = (('error', '配置无法载入，请在更多操作中处理') if self.load_error
                           else mapping_status(self.original, health, now))
            if self.health.get_text() != text:
                self.health.set_text(text)
            if self.health.get_visible() != (level != 'ready'):
                self.health.set_visible(level != 'ready')
            if self.awaiting_saved and level == 'ready':
                self.awaiting_saved = False
                if not self.dirty and self.status.get_text() == '已保存，等待桥接读取':
                    self.message('已保存，桥接已读取')
            if self.detect_until:
                if now >= self.detect_until or not self.is_active():
                    timed_out = now >= self.detect_until and self.last_code is None
                    self.detect.set_active(False)
                    if timed_out:
                        self.message('本次没有检测到按键，请检查蓝牙和桥接状态后重试')
                else:
                    try: write_state('detect.json', {'time': now})
                    except OSError: self.detect.set_active(False)
                    event = read_state('event.json')
                    stamp = event.get('time', 0)
                    if type(stamp) in (int, float) and stamp > self.last_event and 0 <= now - stamp < 2:
                        self.accept_detection(event)
            return True

        def close_requested(self, *_):
            if self.control_busy:
                self.message('后台操作正在进行，完成后即可关闭设置。')
                return True
            self.detect.set_active(False)
            if self.dirty:
                dialog = Gtk.MessageDialog(transient_for=self, modal=True,
                    text='有未保存的按键修改', secondary_text='返回继续编辑，或放弃这些修改并关闭。')
                dialog.add_css_class('remote-settings')
                dialog.add_button('返回编辑', Gtk.ResponseType.CANCEL).add_css_class('secondary-button')
                dialog.add_button('放弃并关闭', Gtk.ResponseType.OK).add_css_class('secondary-button')
                def response(d, result):
                    d.destroy()
                    if result == Gtk.ResponseType.OK:
                        self.dirty = False
                        self.close()
                dialog.connect('response', response)
                dialog.present()
                return True
            GLib.source_remove(self.timer)
            self.closed = True
            if self.status_window:
                self.status_window.close()
            return False

    def activate(app):
        window = app.get_active_window()
        if window is None:
            css = Gtk.CssProvider()
            css.load_from_data(STYLE.encode())
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            window = SettingsWindow(app)
        window.present()

    app = Gtk.Application(application_id='io.github.xiaomi_codex_remote.KeySettings',
                          flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
    app.connect('activate', activate)
    return app.run(sys.argv)


if __name__ == '__main__':
    raise SystemExit(main())
