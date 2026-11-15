# SPDX-License-Identifier: GPL-3.0-only
"""按需获取官方产品图；图片保存在用户缓存，不作为开源素材分发。"""
import hashlib
import os
from pathlib import Path
import tempfile
import urllib.request

PHOTO_URL = 'https://cdn.cnbj0.fds.api.mi-img.com/b2c-shopapi-pms/D5AEE02AB01C470A83B30E0A5F48F73A.png'
PHOTO_PAGE = 'https://www.mi.com/xiaomi-bluetooth-remote-2-pro'
PHOTO_SHA256 = 'cee92fa125135524a2e52814fbf161f59edab3192f1e8ec1cac250421a9d5cca'
MAX_PHOTO_BYTES = 1024 * 1024


def photo_path():
    base = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache')))
    if not base.is_absolute():
        base = Path.home() / '.cache'
    return base / 'xiaomi-codex-remote' / 'product-2-pro.png'


def cached_photo():
    path = photo_path()
    try:
        with path.open('rb') as handle:
            content = handle.read(MAX_PHOTO_BYTES + 1)
        if hashlib.sha256(content).hexdigest() == PHOTO_SHA256:
            return path
    except OSError:
        pass
    return None


def fetch_photo():
    """只取固定地址、限制大小并校验原图；调用者在后台线程执行。"""
    cached = cached_photo()
    if cached:
        return cached
    request = urllib.request.Request(PHOTO_URL, headers={'User-Agent': 'XiaomiCodexRemote/0.1',
                                                        'Referer': PHOTO_PAGE})
    with urllib.request.urlopen(request, timeout=12) as response:
        content = response.read(MAX_PHOTO_BYTES + 1)
    if len(content) > MAX_PHOTO_BYTES or hashlib.sha256(content).hexdigest() != PHOTO_SHA256:
        raise ValueError('产品图内容发生变化，未使用未经核对的图片')
    path = photo_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.product-', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return path
