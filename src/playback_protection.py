# SPDX-License-Identifier: GPL-3.0-only
"""对已核实版本的客户端降音量辅助程序进行可回退保护。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


# 仅验证过此客户端版本的原始辅助程序；未知内容一律拒绝替换。
KNOWN_ORIGINAL_SHA256 = '6a8517562755ee33554c6d5571ba8febfadd188d87b12047a4c70cf230f86186'
BACKUP_SUFFIX = '.xiaomi-codex-original'
METADATA_SUFFIX = '.xiaomi-codex-protection.json'
MAX_FILE_BYTES = 1024 * 1024


class ProtectionError(ValueError):
    """路径、版本或备份不满足保护条件。"""


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _replacement():
    return Path(__file__).with_name('no_duck_helper.py').read_bytes()


def _target(helper):
    path = Path(helper)
    if not path.is_absolute():
        raise ProtectionError('客户端辅助程序必须使用绝对路径')
    path = Path(os.path.abspath(path))
    if path.name != 'dictation-ducking' or path.parent.name != 'native':
        raise ProtectionError('目标必须是 native 目录中的 dictation-ducking 文件')
    return path


def _read_regular(path, private=False):
    """以不跟随文件符号链接的方式读取，并返回实际读取文件的属性。"""
    try:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise ProtectionError(f'目标必须是普通文件，不能是符号链接或其他文件类型：{path}')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                raise ProtectionError(f'文件类型或大小不符合已核实的辅助程序格式：{path}')
            if private and stat.S_IMODE(info.st_mode) & 0o077:
                raise ProtectionError(f'备份或元数据的权限不是用户私有权限：{path}')
            data = handle.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise ProtectionError(f'文件超出允许的大小：{path}')
            return data, info
    except FileNotFoundError as exc:
        raise ProtectionError(f'找不到所需文件：{path}') from exc


def _attributes(info):
    return stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid


def _paths(helper):
    return (helper.with_name(helper.name + BACKUP_SUFFIX),
            helper.with_name(helper.name + METADATA_SUFFIX))


def _sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_atomic(path, data, mode, uid=None, gid=None, *, replace=True):
    """同目录临时文件原子提交；备份使用排他创建，绝不覆盖已有文件。"""
    fd, name = tempfile.mkstemp(prefix='.xiaomi-codex-protection-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            current = os.fstat(handle.fileno())
            if uid is not None and (current.st_uid, current.st_gid) != (uid, gid):
                try:
                    os.fchown(handle.fileno(), uid, gid)
                except PermissionError as exc:
                    raise PermissionError('无法保留原文件属主；请由文件属主或管理员执行此必要步骤') from exc
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            # 创建目标硬链接是排他的，避免检查与写入之间覆盖其他文件。
            os.link(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _check_unchanged(helper, before, info):
    current, now = _read_regular(helper)
    if (current != before or _attributes(now) != _attributes(info)
            or (now.st_dev, now.st_ino) != (info.st_dev, info.st_ino)):
        raise ProtectionError('客户端辅助程序在操作期间发生变化，已停止以避免覆盖')


def _metadata(helper, original, info, replacement):
    mode, uid, gid = _attributes(info)
    return {'version': 1, 'helper': str(helper), 'original_sha256': _digest(original),
            'protected_sha256': _digest(replacement), 'original_mode': mode,
            'original_uid': uid, 'original_gid': gid}


def _validate_backup(helper, info, replacement):
    backup, metadata = _paths(helper)
    original, _ = _read_regular(backup, private=True)
    raw, _ = _read_regular(metadata, private=True)
    try:
        record = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ProtectionError('保护元数据损坏，拒绝继续操作') from exc
    if not isinstance(record, dict) or record.get('version') != 1:
        raise ProtectionError('不支持此保护元数据格式')
    if (record.get('helper') != str(helper)
            or record.get('original_sha256') != KNOWN_ORIGINAL_SHA256
            or _digest(original) != KNOWN_ORIGINAL_SHA256
            or record.get('protected_sha256') != _digest(replacement)):
        raise ProtectionError('原始备份或保护元数据校验失败，拒绝覆盖客户端文件')
    fields = ['original_mode', 'original_uid', 'original_gid']
    if any(type(record.get(key)) is not int or record[key] < 0 for key in fields):
        raise ProtectionError('保护元数据中的权限或属主记录无效')
    if record['original_mode'] > 0o7777:
        raise ProtectionError('保护元数据中的权限记录超出范围')
    if tuple(record[key] for key in fields) != _attributes(info):
        raise ProtectionError('客户端文件的权限或属主已改变，拒绝自动覆盖')
    return original, record


def _result(action, status, helper, original_hash, replacement, message, changed=False):
    backup, metadata = _paths(helper)
    return {'action': action, 'status': status, 'changed': changed, 'helper': str(helper),
            'backup': str(backup), 'metadata': str(metadata), 'original_sha256': original_hash,
            'protected_sha256': _digest(replacement), 'message': message}


def _rollback(helper, before, info, attempted):
    """仅回退本次写入；若出现第三种内容，保留备份并停止覆盖。"""
    current, now = _read_regular(helper)
    if current == before and _attributes(now) == _attributes(info):
        return
    if current != attempted:
        raise ProtectionError('回退时发现其他修改，未覆盖；请保留备份并人工检查')
    _write_atomic(helper, before, *_attributes(info))


def is_protected(helper):
    """精确验证保护脚本内容；缺失、路径不合法或无法读取时返回 False。"""
    try:
        current, _ = _read_regular(_target(helper))
        return current == _replacement()
    except (OSError, ValueError, TypeError):
        return False


def protect(helper, apply=False):
    """默认仅预览；显式应用时备份已知原版并替换，不执行任何服务命令。"""
    helper = _target(helper)
    current, info = _read_regular(helper)
    replacement = _replacement()
    backup, metadata = _paths(helper)
    if current == replacement:
        # 系统目录的备份由管理员私有保存；普通用户仍可只读确认保护内容。
        if apply:
            _validate_backup(helper, info, replacement)
        return _result('protect', 'already_protected', helper, KNOWN_ORIGINAL_SHA256,
                       replacement, '客户端已受保护，未执行写入；恢复时仍需校验私有备份')
    if _digest(current) != KNOWN_ORIGINAL_SHA256:
        raise ProtectionError('客户端降音量辅助程序不是已核实版本，拒绝猜测修改')
    had_backup = backup.exists() or backup.is_symlink()
    had_metadata = metadata.exists() or metadata.is_symlink()
    if had_backup or had_metadata:
        _validate_backup(helper, info, replacement)
    record = _metadata(helper, current, info, replacement)
    raw_metadata = (json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode()
    if not apply:
        return _result('protect', 'preview', helper, _digest(current), replacement,
                       '预览：将备份已核实的原始程序，并替换为不干预音频的辅助程序；当前未写入')
    try:
        if not had_backup:
            _write_atomic(backup, current, 0o600, replace=False)
        if not had_metadata:
            _write_atomic(metadata, raw_metadata, 0o600, replace=False)
        _check_unchanged(helper, current, info)
        _write_atomic(helper, replacement, *_attributes(info))
    except BaseException as exc:
        try:
            _rollback(helper, current, info, replacement)
        except BaseException as rollback_error:
            raise ProtectionError(f'保护失败且无法自动回退，已保留备份：{rollback_error}') from exc
        for path, existed, expected in [(metadata, had_metadata, raw_metadata),
                                        (backup, had_backup, current)]:
            if not existed and path.exists():
                content, _ = _read_regular(path)
                if content == expected:
                    path.unlink()
        raise
    return _result('protect', 'protected', helper, _digest(current), replacement,
                   '客户端音量保护已写入，原始程序已私有备份；未重启任何服务', changed=True)


def restore(helper, apply=False):
    """仅恢复未被更新或另行修改的保护脚本；保留原始备份供审计。"""
    helper = _target(helper)
    current, info = _read_regular(helper)
    replacement = _replacement()
    if current != replacement:
        raise ProtectionError('客户端文件已更新、已恢复或被用户修改，拒绝自动覆盖')
    original, _ = _validate_backup(helper, info, replacement)
    if not apply:
        return _result('restore', 'preview', helper, _digest(original), replacement,
                       '预览：将恢复校验通过的原始客户端辅助程序；当前未写入')
    try:
        _check_unchanged(helper, current, info)
        _write_atomic(helper, original, *_attributes(info))
    except BaseException as exc:
        try:
            _rollback(helper, current, info, original)
        except BaseException as rollback_error:
            raise ProtectionError(f'恢复失败且无法自动回退，已保留备份：{rollback_error}') from exc
        raise
    return _result('restore', 'restored', helper, _digest(original), replacement,
                   '原始客户端辅助程序已恢复，私有备份仍保留；未重启任何服务', changed=True)
