#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""从源码目录打开中文按键设置。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from key_settings import main

if __name__ == '__main__':
    raise SystemExit(main())
