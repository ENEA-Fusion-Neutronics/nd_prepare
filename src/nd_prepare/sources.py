# SPDX-FileCopyrightText: 2026 ENEA
# SPDX-FileContributor: Alberto P
#
# SPDX-License-Identifier: MPL-2.0

"""Fetching and verifying source files: cached curl downloads, local paths, MD5."""

import hashlib
import subprocess
import urllib.parse
from pathlib import Path

from .common import log


def source_cache_name(source):
    """Cache basename of a url source: its "cache_name" if given (needed when two
    sources of one category share a URL basename), else the URL's own."""
    return source.get("cache_name") or Path(urllib.parse.urlparse(source["url"]).path).name


def _file_digest(path, hash_name):
    # Integrity checks, not security: usedforsecurity=False keeps md5 usable on FIPS hosts.
    digest = hashlib.new(hash_name, usedforsecurity=False)
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha512_of_file(path):
    return _file_digest(path, "sha512")


def verify_md5(path, expected_md5, label):
    actual = _file_digest(path, "md5")
    if actual != expected_md5:
        raise RuntimeError(
            f"MD5 mismatch for {label}: expected {expected_md5}, got {actual} (path: {path}); "
            "if the vendor re-published this file, update its \"md5\" in sources.json; "
            "otherwise the file is corrupt, so delete it from cache/ and re-run to re-download it."
        )
    log(f"md5 verified: {label}")


# curl exit codes when the server will not resume a partial download:
# 33 (no byte ranges) and 36 (bad resume offset).
CURL_RESUME_REFUSED = (33, 36)


def _curl(url, part, timeout_seconds, resume):
    cmd = ["curl", "-L", "--fail", "--retry", "3", "--retry-delay", "5"]
    if resume:
        cmd += ["-C", "-"]
    subprocess.run([*cmd, "-o", str(part), "--", url], check=True, timeout=timeout_seconds)


def download(url, dest, md5, timeout_seconds=3600):
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists():
        # dest only exists after a completed download, so the md5 alone decides
        # whether to trust it (keeps fully-cached runs offline).
        log(f"cached: {dest.name}")
        try:
            verify_md5(dest, md5, dest.name)
        except RuntimeError:
            dest.unlink()  # so the next run re-downloads instead of failing again
            raise
        return

    part = dest.with_name(dest.name + ".part")
    # A complete .part is a download whose rename was interrupted; resuming
    # it would ask the server for bytes past the end.
    if part.exists() and _file_digest(part, "md5") == md5:
        log(f"md5 verified: {dest.name} (completed by an earlier run)")
        part.rename(dest)
        return

    log(f"downloading {url} -> {dest}")
    try:
        try:
            _curl(url, part, timeout_seconds, resume=True)
        except subprocess.CalledProcessError as exc:
            if exc.returncode not in CURL_RESUME_REFUSED or not part.exists():
                raise
            # Left in place, the .part would make every later run fail the same way.
            log(f"the server cannot resume {dest.name}; downloading it again from the start")
            part.unlink()
            _curl(url, part, timeout_seconds, resume=False)
    except FileNotFoundError as exc:
        raise RuntimeError("curl not found on PATH; it is required to download sources") from exc
    try:
        verify_md5(part, md5, dest.name)
    except RuntimeError:
        part.unlink()
        raise
    part.rename(dest)


def resolve_archive_source(archive, cache_dir):
    """Return a local path for a source spec (any kind), md5-verified.

    A source is either downloaded via "url" (cached under cache_dir) or read
    from a local "path" (relative to the current directory) for data that is
    not published online. The md5 is checked on every call.
    """
    md5 = archive["md5"]

    if "path" in archive:
        local_path = Path(archive["path"]).expanduser()
        if not local_path.is_absolute():
            local_path = Path.cwd() / local_path
        if not local_path.is_file():
            raise RuntimeError(
                f"local archive not found: {local_path} "
                "(expected in the current working directory)"
            )
        # Never deleted on a mismatch: it is the user's file, not a cached download.
        verify_md5(local_path, md5, local_path.name)
        return local_path

    cached = cache_dir / source_cache_name(archive)
    download(archive["url"], cached, md5)
    return cached


def source_label(source):
    return source_cache_name(source) if "url" in source else Path(source["path"]).name
