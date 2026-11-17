#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""运行已安装的 SayAll 桥接；BLE 断开时退出，让 systemd 重建连接。"""
import asyncio
import importlib.machinery
import importlib.util
from pathlib import Path
import time


async def run_with_disconnect_guard(bridge, operation, interval=0.5, *, hid_timeout=15):
    async def watch_connection():
        missing_since = None
        while True:
            client = bridge.client
            if client is not None and not client.is_connected:
                raise ConnectionError('遥控器 BLE 连接已断开，需要重建音频订阅')
            hid = getattr(bridge, 'hid', None)
            if hid is not None and getattr(hid, 'dev', None) is None:
                if missing_since is None:
                    missing_since = time.monotonic()
                elif time.monotonic() - missing_since >= hid_timeout:
                    raise ConnectionError('遥控器 HID 输入持续不可用，需要重新检查连接')
            else:
                missing_since = None
            await asyncio.sleep(interval)

    running = asyncio.create_task(operation)
    watching = asyncio.create_task(watch_connection())
    try:
        done, _ = await asyncio.wait((running, watching), return_when=asyncio.FIRST_COMPLETED)
        # 正常停止和初始化错误继续按原有方式处理。
        if running in done:
            return running.result()
        return watching.result()
    finally:
        for task in (running, watching):
            if not task.done():
                task.cancel()
        await asyncio.gather(running, watching, return_exceptions=True)


def contextual_remote_hid(base_hid, focus, config=None, mappings=None):
    from contextual_back import BackspaceCapabilities, ContextualBackKeyboard
    from evdev import ecodes

    from mapping_keyboard import MappingCapabilities, MappingKeyboard
    from key_detection import EventDevice

    class ContextualRemoteHid(base_hid):
        def _attach(self):
            from startup_guard import attach_released_keyboard
            return attach_released_keyboard(self)

        def _read_events(self):
            if mappings is None:
                return super()._read_events()
            original = self.dev
            proxy = EventDevice(original, self.ui, mappings, focus)
            self.dev = proxy
            try:
                return super()._read_events()
            finally:
                if self.dev is proxy:
                    self.dev = original

        @staticmethod
        def find_keyboard():
            candidates = base_hid.find_keyboard()
            if config is None:
                return candidates
            selected = []
            for device in candidates:
                keep = False
                try:
                    keep = (device.uniq.lower() == config.remote_address
                            and device.info.vendor == config.vendor_id
                            and device.info.product == config.product_id
                            and ecodes.KEY_F5 in device.capabilities().get(ecodes.EV_KEY, []))
                    if keep:
                        selected.append(device)
                except (OSError, SystemError):
                    pass
                finally:
                    if not keep:
                        try:
                            device.close()
                        except (OSError, SystemError):
                            pass
            return selected

        def _ensure_uinput(self, device):
            if self.ui is not None:
                return
            super()._ensure_uinput((MappingCapabilities if mappings is not None else BackspaceCapabilities)(device))
            if self.ui is not None:
                self.ui = (MappingKeyboard(self.ui, focus, mappings, self.log) if mappings is not None
                           else ContextualBackKeyboard(self.ui, focus, self.log))

        def _detach(self):
            if isinstance(self.ui, MappingKeyboard):
                self.ui.detach()
            elif isinstance(self.ui, ContextualBackKeyboard):
                self.ui.release()
            super()._detach()

    return ContextualRemoteHid


def main():
    from remote_config import load_config
    config = load_config()
    # 返回键复用听写／发送桥接的应用上下文，微信扩展仍须显式启用。
    keys_path = Path(__file__).with_name('codex-ptt-key.py')
    keys_spec = importlib.util.spec_from_file_location('codex_keys', keys_path)
    keys = importlib.util.module_from_spec(keys_spec)
    keys_spec.loader.exec_module(keys)

    loader = importlib.machinery.SourceFileLoader('installed_sayall', config.sayall_bridge)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    upstream = importlib.util.module_from_spec(spec)
    loader.exec_module(upstream)
    if config.sayall_config is not None:
        upstream.CONFIG_PATH = config.sayall_config
    from startup_guard import guarded_bridge
    upstream.Bridge = guarded_bridge(upstream.Bridge, config, run_with_disconnect_guard)
    from keymap_config import MappingStore
    upstream.RemoteHid = contextual_remote_hid(upstream.RemoteHid, keys.focused_window_context, config,
                                               mappings=MappingStore())
    return upstream.main()


if __name__ == '__main__':
    raise SystemExit(main())
