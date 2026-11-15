# SPDX-License-Identifier: GPL-3.0-only
"""产品图获取与失败恢复测试；不访问网络或使用真实图片。"""
import support
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import product_photo as P


class ProductPhotoTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name) / 'cache' / 'product.png'
        self.content = b'isolated-image-response'
        for context in (patch.object(P, 'photo_path', return_value=self.cache),
                        patch.object(P, 'PHOTO_SHA256', hashlib.sha256(self.content).hexdigest())):
            context.start()
            self.addCleanup(context.stop)

    def test_verified_cache_works_offline(self):
        self.cache.parent.mkdir()
        self.cache.write_bytes(self.content)
        with patch.object(P.urllib.request, 'urlopen', side_effect=AssertionError('不应联网')):
            self.assertEqual(P.fetch_photo(), self.cache)

    def test_verified_download_is_atomic_and_reused(self):
        with patch.object(P.urllib.request, 'urlopen', return_value=io.BytesIO(self.content)) as download:
            self.assertEqual(P.fetch_photo(), self.cache)
            self.assertEqual(P.fetch_photo(), self.cache)
            self.assertEqual(download.call_count, 1)
        self.assertEqual(self.cache.read_bytes(), self.content)
        self.assertEqual(list(self.cache.parent.iterdir()), [self.cache])

    def test_changed_or_oversized_image_never_overwrites_cache(self):
        self.cache.parent.mkdir()
        self.cache.write_bytes(b'old-invalid-cache')
        for content in (b'changed-server-image', b'x' * (P.MAX_PHOTO_BYTES + 1)):
            with self.subTest(size=len(content)), patch.object(P.urllib.request, 'urlopen', return_value=io.BytesIO(content)):
                with self.assertRaises(ValueError):
                    P.fetch_photo()
            self.assertEqual(self.cache.read_bytes(), b'old-invalid-cache')

    def test_failed_request_does_not_leave_partial_cache(self):
        with patch.object(P.urllib.request, 'urlopen', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                P.fetch_photo()
        self.assertFalse(self.cache.exists())


if __name__ == '__main__':
    unittest.main()
