#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""保留客户端听写辅助程序的生命周期，不干预任何音频输入或输出。"""
import sys


def main():
    # 标准输入仅用于生命周期控制；客户端关闭管道后退出。
    # 不访问音频设备，不调用外部命令，也不读取或设置音量。
    while sys.stdin.buffer.read(1):
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
