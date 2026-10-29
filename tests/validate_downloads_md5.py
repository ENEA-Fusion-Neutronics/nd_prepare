#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ENEA
# SPDX-FileContributor: Alberto P
#
# SPDX-License-Identifier: MPL-2.0
"""md5 check: every nd_manager installed file against its kept download archive.

Streams each archive in <downloads>/<repository>/<LIB>/ (the "keep downloads"
option) and compares every member with the file nd_manager installed from it
under <data>. Exits 1 on any MISMATCH, MISSING file or badly laid-out archive.

Member -> installed path follows nd_manager's install_lib: the root folder is
dropped, hidden files are skipped, and xsdir, xsdir_2.0 and README.md become
database/xsdir_<LIB>, database/xsdir_2.0_<LIB> and docs/README_<LIB>.md.
Files in <data> that no archive accounts for are validate_md5.py's business.

Usage: python3 validate_downloads_md5.py --downloads DIR --data DIR [--jobs N] [--only LIB ...]
"""

import argparse
import multiprocessing
import sys
from pathlib import Path

from validation_common import ARCHIVE_SUFFIXES, CONSOLE_MAX, iter_members, md5_stream


def installed_relpath(lib, parts):
    """Where install_lib puts a member (path parts below the root folder)."""
    if len(parts) == 1:
        renamed = {
            "xsdir": Path("database", f"xsdir_{lib}"),
            "xsdir_2.0": Path("database", f"xsdir_2.0_{lib}"),
            "README.md": Path("docs", f"README_{lib}.md"),
        }
        if parts[0] in renamed:
            return renamed[parts[0]]
    return Path(*parts)


def check_archive(job):
    """(lib, archive name, member count, problems) for one archive."""
    lib, path, data = job
    problems, roots, count = [], set(), 0
    try:
        for name, handle in iter_members(path):
            parts = [p for p in name.split("/") if p not in ("", ".")]
            if not parts or parts[-1].startswith("."):
                continue
            roots.add(parts[0])
            if len(parts) < 2:
                problems.append(f"{name}: not inside a root folder")
                continue
            rel = installed_relpath(lib, parts[1:])
            count += 1
            try:
                with open(data / rel, "rb") as installed:
                    installed_md5 = md5_stream(installed)[0]
            except (FileNotFoundError, NotADirectoryError):
                problems.append(f"{rel}: MISSING")
                continue
            if installed_md5 != md5_stream(handle)[0]:
                problems.append(f"{rel}: MISMATCH")
    except Exception as error:  # tarfile, zipfile, zlib and OS errors all mean "unreadable"
        problems.append(f"cannot read archive: {error}")
    if len(roots) > 1:
        problems.append(f"{len(roots)} root folders: not installable")
    return lib, path.name, count, problems


def find_kept_downloads(downloads, only):
    """(lib, path) for every archive in <downloads>/<repository>/<LIB>/."""
    found = sorted(
        (p.parent.name, p)
        for p in downloads.rglob("*")
        if p.is_file() and p.name.endswith(ARCHIVE_SUFFIXES)
    )
    unknown = set(only or ()) - {lib for lib, _ in found}
    if unknown:
        raise SystemExit(f"error: no kept download for: {', '.join(sorted(unknown))}")
    return [(lib, p) for lib, p in found if not only or lib in only]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--downloads", required=True, help="nd_manager 'download path'.")
    parser.add_argument("--data", required=True, help="nd_manager 'data path'.")
    parser.add_argument("--jobs", type=int, default=4, help="Parallel archives (default 4).")
    parser.add_argument("--only", nargs="+", metavar="LIB", help="Only check these libraries.")
    args = parser.parse_args()

    downloads, data = Path(args.downloads).resolve(), Path(args.data).resolve()
    for label, path in (("downloads", downloads), ("data", data)):
        if not path.is_dir():
            raise SystemExit(f"error: --{label} {path} is not a directory")
    archives = find_kept_downloads(downloads, args.only)
    if not archives:
        raise SystemExit(f"error: no archives found under {downloads}")

    problems, total = [], 0
    with multiprocessing.Pool(args.jobs) as pool:
        jobs = [(lib, path, data) for lib, path in archives]
        for lib, name, count, found in pool.imap_unordered(check_archive, jobs):
            total += count
            print(f"{lib}/{name}: {count} files, {'OK' if not found else f'{len(found)} PROBLEM(S)'}", flush=True)
            problems += [f"{lib}/{name}: {problem}" for problem in found]
    if problems:
        print(f"FAILED: {len(problems)} problem(s):")
        for problem in problems[:CONSOLE_MAX]:
            print(f"    {problem}")
        return 1
    print(f"PASSED: {total} files in {len(archives)} archives match what nd_manager installed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
