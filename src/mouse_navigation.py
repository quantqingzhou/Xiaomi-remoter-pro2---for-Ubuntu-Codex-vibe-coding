# SPDX-License-Identifier: GPL-3.0-only
"""遥控器鼠标状态机。所有入口由 MappingKeyboard 的锁串行调用。

先在原始 HID 层截获，再进入 SayAll：确认键不能旁路发送，语音键不能旁路听写。
普通模式不创建虚拟鼠标；长按进入只依赖按下/松开，不依赖设备的重复事件。
"""
import math
import time
from evdev import ecodes as E
from keymap_config import navigation_options

LONG_PRESS = 0.6
MAX_MOTION_HOLD = 10.0
DIRECTIONS = {'left': (-1, 0), 'right': (1, 0), 'up': (0, -1), 'down': (0, 1)}
MOUSE_BUTTONS = set(DIRECTIONS) | {'confirm', 'menu', 'back', 'volume_up', 'volume_down', 'voice'}


def create_mouse():
    from evdev import UInput
    class WriteOnlyMouse(UInput):
        # 只需向 uinput 写事件。默认反向打开 event 节点会因桌面权限等待数秒，
        # 既无必要，也不应为此扩大用户对其他输入设备的读取权限。
        def _find_device(self, _fd):
            return None
    return WriteOnlyMouse({E.EV_KEY: [E.BTN_LEFT, E.BTN_RIGHT],
                   E.EV_REL: [E.REL_X, E.REL_Y, E.REL_WHEEL]},
                  name='SayAll Remote Mouse', vendor=1, product=2, version=1)


class MouseNavigation:
    def __init__(self, mappings, tap, release_keys, *, mouse_factory=create_mouse,
                 indicator=None, clock=time.monotonic, log=print):
        if indicator is None:
            from mouse_indicator import MouseIndicator
            indicator = MouseIndicator(log=log)
        self.mappings, self.tap, self.release_keys = mappings, tap, release_keys
        self.mouse_factory, self.indicator, self.clock, self.log = mouse_factory, indicator, clock, log
        self.mouse = None
        self.active = False
        self.context = None
        self.owned = {}
        self.physical = set()
        self.last_tick = clock()
        self.last_status = 0.0
        self.fraction = [0.0, 0.0]
        self.hint = ''
        self.hint_until = 0.0

    def _message(self, text):
        self.hint, self.hint_until = text, self.clock() + 3
        self.publish()

    def publish(self):
        self.last_status = self.clock()
        ok = self.indicator.update(self.active, self.context,
                                   self.hint if self.clock() < self.hint_until else '')
        if self.active and not ok:
            self.cancel('模式提示不可用，已退出鼠标模式', publish=False)
        return ok

    def _click(self, code):
        # 按下/松开在同一次操作内完成；长按确认不持有鼠标左键。
        try:
            self.mouse.write(E.EV_KEY, code, 1)
            self.mouse.syn()
        finally:
            self.mouse.write(E.EV_KEY, code, 0)
            self.mouse.syn()

    def _scroll(self, button):
        self.mouse.write(E.EV_REL, E.REL_WHEEL, 1 if button == 'volume_up' else -1)
        self.mouse.syn()

    def cancel(self, reason='', *, publish=True):
        was_active = self.active
        self.active, self.context = False, None
        for state in self.owned.values():
            state['cancelled'] = True
        self.fraction = [0.0, 0.0]
        if was_active and reason:
            self.log('鼠标模式：' + reason)
        if publish:
            self.publish()

    def _toggle(self, context):
        self.release_keys()
        if self.active:
            self.cancel('已返回普通模式')
            return
        try:
            if self.mouse is None:
                self.mouse = self.mouse_factory()
            self.active, self.context = True, context
            self.last_tick = self.clock()
            self.hint, self.hint_until = '', 0
            if self.publish():
                self.log('鼠标模式：已进入（长按主页退出）')
        except Exception as exc:
            self.cancel(publish=False)
            self.log(f'鼠标模式未启动：{exc}')

    def change_context(self, context):
        """模式全局保留，但已按住的动作不能跨应用继续或补发点击。"""
        if not self.active or self.context == context:
            return
        self.release_keys()
        for state in self.owned.values():
            state['cancelled'] = True
        self.fraction = [0.0, 0.0]
        self.context = context
        self.hint, self.hint_until = '', 0
        self.publish()

    def reconcile(self, pressed):
        """物理状态兜底：漏了松开事件也立即停止移动，不补发点击。"""
        if pressed is None:
            self.cancel('遥控器按键状态不可用')
            return
        self.physical = set(pressed)
        for code, state in self.owned.items():
            if code not in self.physical:
                # EVIOCGKEY 可比队列里的松开事件早到；留出事件派发窗口。
                missing = state.setdefault('missing_since', self.clock())
                if self.clock() - missing >= 0.16:
                    state['cancelled'] = True
            else:
                state.pop('missing_since', None)

    def handle(self, event, context):
        if event.type != E.EV_KEY:
            return False
        code, value = event.code, event.value
        if value == 1:
            self.physical.add(code)
        elif value == 0:
            self.physical.discard(code)
        state = self.owned.get(code)
        if state is not None:
            if value == 0:
                # 用释放时长也能识别长按，防止事件循环短暂繁忙导致误发 Super/左键。
                self._advance(code, state, context, self.clock())
                self.owned.pop(code, None)
                if not state['cancelled'] and not state['done']:
                    if state['button'] == 'home':
                        self.tap(state['codes'])
                    elif state['button'] == 'confirm' and self.active:
                        self._click(E.BTN_LEFT)
            return True
        options = navigation_options(self.mappings.data)
        button = self.mappings.button(code)
        # SayAll 0.3.0 将 191 命名为 F20；使用它实际识别的码，不能用 evdev.KEY_F20(190)。
        if self.active and code in (E.KEY_REFRESH, 191):
            button = 'voice'
        if self.active and button == 'back' and context in ('wechat', 'claude'):
            # 微信、Claude 输入框在鼠标模式仍可编辑；普通映射负责重复与松开。
            return False
        home = button == 'home' and options['enabled']
        if value != 1 or not (home or (self.active and button in MOUSE_BUTTONS)):
            return False
        state = {'button': button, 'since': self.clock(), 'context': context,
                 'codes': self.mappings.codes('home', context, code) if home else (),
                 'done': False, 'cancelled': False, 'next_scroll': self.clock() + 0.4}
        self.owned[code] = state
        if button in ('volume_up', 'volume_down'):
            self._scroll(button)
        elif button == 'menu':
            # 独立右键在按下时完成一对点击；吞掉重复和松开，不触发普通菜单映射。
            state['done'] = True
            self._click(E.BTN_RIGHT)
        elif button == 'back':
            self.tap((E.KEY_ESC,))
        elif button == 'voice':
            self._message('先点击输入框，长按主页退出鼠标模式，再听写')
        elif button in DIRECTIONS:
            dx, dy = DIRECTIONS[button]
            for axis, delta in ((E.REL_X, dx * 2), (E.REL_Y, dy * 2)):
                if delta:
                    self.mouse.write(E.EV_REL, axis, delta)
            self.mouse.syn()
        return True

    def _advance(self, code, state, context, now):
        if state['cancelled']:
            return
        age = now - state['since']
        if state['context'] != context or age >= MAX_MOTION_HOLD:
            state['cancelled'] = True
            return
        if state['done'] or age < LONG_PRESS:
            return
        if state['button'] == 'home':
            state['done'] = True
            if self.physical - {code}:
                self.log('鼠标模式未切换：请先松开其他按键')
                return
            self._toggle(context)
        elif state['button'] == 'confirm' and self.active:
            state['done'] = True
            self._click(E.BTN_RIGHT)

    def tick(self, context):
        now = self.clock()
        dt = max(0, min(now - self.last_tick, 0.05))
        self.last_tick = now
        options = navigation_options(self.mappings.data)
        if self.active and not options['enabled']:
            self.cancel('已关闭功能')
        self.change_context(context)
        for code, state in list(self.owned.items()):
            self._advance(code, state, context, now)
        if not self.active:
            return
        x = y = 0
        oldest = now
        for state in self.owned.values():
            if state['cancelled']:
                continue
            button = state['button']
            if button in DIRECTIONS:
                dx, dy = DIRECTIONS[button]
                x, y = x + dx, y + dy
                oldest = min(oldest, state['since'])
            elif button in ('volume_up', 'volume_down') and now >= state['next_scroll']:
                self._scroll(button)
                state['next_scroll'] = now + (0.5, 0.33, 0.22, 0.14, 0.1)[options['scroll_speed'] - 1]
        length = math.hypot(x, y)
        if length and now - oldest > 0.12:
            speed = (150, 300, 500, 800, 1100)[options['pointer_speed'] - 1]
            speed *= min(1, 0.2 + (now - oldest) / 0.8)
            for index, (axis, direction) in enumerate(((E.REL_X, x), (E.REL_Y, y))):
                total = self.fraction[index] + direction / length * speed * dt
                delta = int(total)
                self.fraction[index] = total - delta
                if delta:
                    self.mouse.write(E.EV_REL, axis, delta)
            self.mouse.syn()
        elif not length:
            self.fraction = [0.0, 0.0]
        if now - self.last_status >= 0.4:
            self.publish()

    def close(self):
        self.cancel()
        self.owned.clear()
        self.physical.clear()
        self.indicator.close()
        if self.mouse is not None:
            self.mouse.close()
            self.mouse = None
