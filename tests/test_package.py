# SPDX-License-Identifier: GPL-3.0-only
"""源码归档应可复现，且不带入运行数据。"""
import support
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile
import package


class PackageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'source'
        self.root.mkdir()
        (self.root / 'VERSION').write_text('0.1.0-alpha.6\n')
        (self.root / 'README.md').write_text('中文说明\n')

    def test_archives_match_source_and_are_reproducible(self):
        one = package.build(self.root, self.root / 'dist')
        data = [p.read_bytes() for p in one]
        two = package.build(self.root, self.root / 'dist')
        self.assertEqual(data, [p.read_bytes() for p in two])
        prefix = 'xiaomi-codex-remote-0.1.0-alpha.6/'
        with tarfile.open(one[0]) as tar, zipfile.ZipFile(one[1]) as archive:
            self.assertEqual(sorted(tar.getnames()), sorted(archive.namelist()))
            for name in ('VERSION', 'README.md'):
                self.assertEqual(tar.extractfile(prefix + name).read(), (self.root / name).read_bytes())
                self.assertEqual(archive.read(prefix + name), (self.root / name).read_bytes())

    def test_private_runtime_files_are_excluded(self):
        for name in ('.env', 'diagnostics/private.json', 'backups/token.md', 'src/__pycache__/private.py'):
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('private')
        self.assertEqual({p.name for p in package.source_files(self.root)}, {'VERSION', 'README.md'})

    def test_source_symlinks_and_invalid_versions_are_refused(self):
        (self.root / 'LICENSE').symlink_to(self.root / 'README.md')
        with self.assertRaises(ValueError): package.build(self.root, self.root / 'dist')
        (self.root / 'LICENSE').unlink()
        (self.root / 'VERSION').write_text('../../private')
        with self.assertRaises(ValueError): package.build(self.root, self.root / 'dist')
