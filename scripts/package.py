#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""只打包公开源码，生成可复现的归档与 SHA-256，不安装或访问设备。"""
import argparse
import gzip
import hashlib
import io
from pathlib import Path
import re
import tarfile
import zipfile

ROOT_FILES = {'.gitignore', 'README.md', 'README.en.md', 'CHANGELOG.md',
              'CONTRIBUTING.md', 'THIRD_PARTY.md', 'LICENSE', 'VERSION', 'requirements.txt'}
SOURCE_DIRS = {'.github', 'docs', 'examples', 'scripts', 'src', 'tests'}
SOURCE_SUFFIXES = {'.py', '.md', '.json', '.yml', '.yaml', '.svg'}


def source_files(root):
    result = []
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if ('__pycache__' in relative.parts or not path.is_file()
                or (str(relative) not in ROOT_FILES and not (
                    relative.parts[0] in SOURCE_DIRS and path.suffix in SOURCE_SUFFIXES))):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('源码包不接受符号链接：' + str(relative))
        if path.name in {'config.local.json', 'install-state.json', 'keymap.json'}:
            raise ValueError('发现私人配置：' + str(relative))
        result.append(path)
    return result


def build(root, output):
    root, output = Path(root), Path(output)
    version = (root / 'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('版本号格式不正确')
    name = 'xiaomi-codex-remote-' + version
    files = source_files(root)
    contents = [(name + '/' + str(p.relative_to(root)), p.read_bytes()) for p in files]
    output.mkdir(parents=True, exist_ok=True)
    tar_path, zip_path = output / (name + '.tar.gz'), output / (name + '.zip')
    with tar_path.open('wb') as stream, gzip.GzipFile(filename='', fileobj=stream, mode='wb', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as archive:
            for entry, data in contents:
                info = tarfile.TarInfo(entry)
                info.size, info.mode, info.mtime = len(data), 0o644, 0
                archive.addfile(info, io.BytesIO(data))
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for entry, data in contents:
            info = zipfile.ZipInfo(entry, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    checksum = output / (name + '.sha256')
    checksum.write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n'
                                for p in (tar_path, zip_path)))
    return tar_path, zip_path, checksum


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('dist'), help='归档输出目录')
    args = parser.parse_args()
    for artifact in build(Path(__file__).resolve().parents[1], args.output):
        print(artifact)
