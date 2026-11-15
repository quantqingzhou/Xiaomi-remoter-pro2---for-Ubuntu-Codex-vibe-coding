#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""点击可穿透的鼠标模式提示条；不使用共享桌面或远程控制门户。"""
import os
os.environ['GDK_BACKEND'] = 'x11'
import time
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib
import cairo
from key_detection import read_state


def main():
    ok, _ = Gtk.init_check([])
    if not ok:
        return 1
    window = Gtk.Window(type=Gtk.WindowType.POPUP)
    window.set_title('小米遥控器 · 鼠标模式')
    window.set_wmclass('xiaomi-remote-mouse', 'XiaomiRemoteMouse')
    window.set_decorated(False)
    window.set_resizable(False)
    window.set_default_size(1, 1)
    window.set_accept_focus(False)
    window.set_focus_on_map(False)
    window.set_skip_taskbar_hint(True)
    window.set_skip_pager_hint(True)
    window.set_keep_above(True)
    window.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
    visual = window.get_screen().get_rgba_visual()
    if visual:
        window.set_visual(visual)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.set_border_width(12)
    title = Gtk.Label(label='全局鼠标模式 · 长按主页退出')
    help_text = Gtk.Label(label='方向键移动 · 确认点击 · 音量键滚动')
    box.pack_start(title, False, False, 0)
    box.pack_start(help_text, False, False, 0)
    window.add(box)
    css = Gtk.CssProvider()
    css.load_from_data(b'window { background: rgba(244,248,250,0.96); border: 1px solid #9db8c4; border-radius: 14px; } label { color: #264858; font-size: 13px; }')
    Gtk.StyleContext.add_provider_for_screen(window.get_screen(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    def realized(*_):
        window.input_shape_combine_region(cairo.Region())
        window.get_window().input_shape_combine_region(cairo.Region(), 0, 0)
    window.connect('realize', realized)
    window.realize()
    # 主进程收到 ready 后才允许进入鼠标模式。
    print('ready', flush=True)
    started = time.monotonic()
    last_text = None
    def update():
        nonlocal last_text
        state = read_state('navigation.json')
        now = time.monotonic()
        stamp = state.get('time', 0)
        fresh = type(stamp) in (float, int) and 0 <= now - stamp < 2
        if not fresh:
            window.hide()
            if now - started > 4:
                Gtk.main_quit()
                return False
        elif state.get('active'):
            text = state.get('hint') or '方向键移动 · 确认点击 · 音量键滚动'
            if text != last_text:
                help_text.set_text(text)
                last_text = text
            if not window.get_visible():
                display = window.get_display()
                pointer = display.get_default_seat().get_pointer()
                _, x, y = pointer.get_position()
                monitor = display.get_monitor_at_point(x, y)
                area = monitor.get_workarea()
                window.show_all()
                # GTK 在映射窗口时可能重设输入区域，显示之后再应用穿透形状。
                realized()
                width, height = window.get_size()
                window.move(area.x + area.width - width - 18, area.y + 18)
        else:
            window.hide()
        return True
    GLib.timeout_add(100, update)
    Gtk.main()
    window.destroy()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
