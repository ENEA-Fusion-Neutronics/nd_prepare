# SPDX-FileCopyrightText: 2026 ENEA
# SPDX-FileContributor: Alberto P
#
# SPDX-License-Identifier: MPL-2.0

"""Safe extraction of zip and tar archives (regular files only)."""

import shutil
import tarfile
import zipfile
from pathlib import Path

from .common import log

JUNK_DIR_NAMES = {"__MACOSX"}


def _member_name(raw_name, strip_components):
    """Normalized relative name of an archive member after dropping its leading
    components, or None if nothing is left or it is junk. Raises on a
    zip-slip / path-traversal name."""
    parts = Path(raw_name).parts
    if parts and (parts[0] == "/" or ".." in parts):
        raise RuntimeError(f"unsafe path in archive: {raw_name}")
    if any(part.startswith(".") or part in JUNK_DIR_NAMES for part in parts):
        return None
    parts = parts[strip_components:]
    return Path(*parts).as_posix() if parts else None


def _write_member(src, dest_dir, name):
    target = dest_dir / name
    target.parent.mkdir(parents=True, exist_ok=True)
    with src, open(target, "wb") as out:
        shutil.copyfileobj(src, out)


def extract_zip(archive_path, dest_dir, strip_components=0):
    written = []
    with zipfile.ZipFile(archive_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = _member_name(info.filename, strip_components)
            if name is not None:
                _write_member(zf.open(info), dest_dir, name)
                written.append(name)
    return written


def extract_tar(archive_path, dest_dir, strip_components=0):
    written = []
    # Stream mode ("r|*") decompresses the archive once, front to back; random
    # access would decompress a gzip stream again on every seek back.
    with tarfile.open(archive_path, mode="r|*") as tf:
        for member in tf:
            if not member.isfile():
                if member.issym() or member.islnk():
                    log(f"skipping link member: {member.name}")
                continue
            name = _member_name(member.name, strip_components)
            if name is not None:
                _write_member(tf.extractfile(member), dest_dir, name)
                written.append(name)
    return written


def extract_archive(archive_path, kind, dest_dir, strip_components=0):
    """Extract an archive's regular files into dest_dir; return the relative
    names written."""
    if kind == "zip":
        return extract_zip(archive_path, dest_dir, strip_components)
    if kind == "tar":
        return extract_tar(archive_path, dest_dir, strip_components)
    raise ValueError(f"unknown archive kind: {kind}")
