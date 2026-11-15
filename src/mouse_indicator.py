# SPDX-License-Identifier: GPL-3.0-only
"""仅在首次进入鼠标模式时启动独立的、不抢焦点的 XWayland 状态条。"""
import os
from pathlib import Path
import select
import subprocess
import sys
import time
from key_detection import write_state


class MouseIndicator:
    def __init__(self, log=print):
        self.process = None
        self.log = log

    def update(self, active, context, hint=''):
        try:
            if not active:
                if self.process is not None:
                    write_state('navigation.json', {'time': time.monotonic(), 'active': False})
                    self.close()
                return True
            if self.process is not None and self.process.poll() is not None:
                self.close()
                raise OSError('鼠标模式提示条已退出，请重新长按主页键')
            if active and self.process is None:
                write_state('navigation.json', {'time': time.monotonic(), 'active': False})
                env = {**os.environ, 'GDK_BACKEND': 'x11'}
                self.process = subprocess.Popen(
                    [sys.executable, '-B', str(Path(__file__).with_name('mouse_overlay.py'))],
                    env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=None, close_fds=True)
                ready, _, _ = select.select([self.process.stdout], [], [], 3)
                if not ready or self.process.stdout.readline() != b'ready\n':
                    raise OSError('无法打开鼠标模式提示条（需要 XWayland 和 GTK 3）')
                self.process.stdout.close()
            if active and self.process.poll() is not None:
                raise OSError('鼠标模式提示条已退出')
            write_state('navigation.json', {'time': time.monotonic(), 'active': active,
                        'context': context, 'hint': hint})
            return True
        except (OSError, ValueError) as exc:
            self.log(str(exc))
            self.close()
            return False

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=0.5)
            if self.process.stdout is not None:
                self.process.stdout.close()
            self.process = None
