#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ENEA
# SPDX-FileContributor: Alberto P
#
# SPDX-License-Identifier: MPL-2.0
"""Independent md5 check: every installed ACE file against the vendor archives.

Streams every member of every archive in <workdir>/cache/ and every archive
placed directly in <workdir>, and md5s it in memory. Then md5s every file under
<workdir>/MCNP_DATA/<LIB>/ (what nd_manager installed) and classifies it:

  MATCH (same name)  an archive member with the same base name and md5
  MATCH (renamed)    an archive member with the same md5 under another name
  DIFFERS            a member with the same name but other content
  NOT FOUND          no archive member has this md5 or name

It also checks that the copy under <workdir>/work/<LIB>/payload/<LIB>/ (when
that directory still exists) is byte-identical to the installed copy.

Exits 1 on any DIFFERS, NOT FOUND, unreadable archive, work != MCNP_DATA, or a
library of validation_expected.json that is not installed (not checked with
--only).

Usage: python3 validate_md5.py [--workdir DIR] [--jobs N] [--only LIB ...]
Reports: <out>/md5_report.md and <out>/ace_origin_check.csv
"""

import argparse
import json
import multiprocessing
import sys
from collections import Counter, defaultdict, namedtuple
from pathlib import Path

from validation_common import (
    REPORT_MAX,
    add_common_args,
    find_archives,
    finish,
    hash_archive,
    installed_libraries,
    md5_stream,
    md_list,
    md_table,
    missing_libraries,
    resolve_paths,
    write_csv,
)

MATCH_SAME = "MATCH (same name)"
MATCH_RENAMED = "MATCH (renamed)"
DIFFERS = "DIFFERS"
NOT_FOUND = "NOT FOUND"
OUTCOMES = (MATCH_SAME, MATCH_RENAMED, DIFFERS, NOT_FOUND)
WORK_DIFFERS = "work != MCNP_DATA"

# One member of one vendor archive.
Member = namedtuple("Member", "group archive name md5")


def hash_archive_job(job):
    """(group, archive name, members, error) for one archive; a corrupt
    archive gives an error message instead of aborting the whole pool."""
    group, path = job
    try:
        return group, path.name, hash_archive(path), None
    except Exception as error:  # tarfile, zipfile, zlib and OS errors all mean "unreadable"
        return group, path.name, [], str(error)


def hash_file_job(path):
    with open(path, "rb") as handle:
        return path, md5_stream(handle)[0]


def hash_all_archives(workdir, jobs):
    """([Member], [problem], archive count) for every archive member and
    unreadable archive."""
    archives = find_archives(workdir)
    if not archives:
        raise SystemExit(f"error: no archives found under {workdir}/cache or {workdir}")
    print(f"hashing {len(archives)} archives with {jobs} worker(s)", flush=True)
    members, problems = [], []
    with multiprocessing.Pool(jobs) as pool:
        for group, archive, found, error in pool.imap_unordered(hash_archive_job, archives):
            members += [Member(group, archive, name, digest) for name, _size, digest in found]
            if error:
                problems.append(f"{archive}: cannot read archive: {error}")
            print(f"  {archive}: {len(found)} members", flush=True)
    return members, problems, len(archives)


def classify(lib, base, digest, by_name, by_md5):
    """(outcome, archive Member or None, note) for one installed file."""
    same_name = [m for m in by_name.get(base, []) if m.md5 == digest]
    if same_name:
        mine = [m for m in same_name if m.group == lib]
        return MATCH_SAME, (mine or same_name)[0], ""
    renamed = by_md5.get(digest, [])
    if renamed:
        mine = [m for m in renamed if m.group == lib]
        return MATCH_RENAMED, (mine or renamed)[0], ""
    other = by_name.get(base, [])
    if other:
        return DIFFERS, other[0], f"archive md5 {other[0].md5} ({len(other)} archive copies)"
    return NOT_FOUND, None, ""


def index_members(archive_members):
    """({base name: [Member]}, {md5: [Member]})."""
    by_name, by_md5 = defaultdict(list), defaultdict(list)
    for member in archive_members:
        by_name[Path(member.name).name].append(member)
        by_md5[member.md5].append(member)
    return by_name, by_md5


def hash_installed(workdir, libs, jobs):
    """({lib: installed files}, {lib: work payload dir}, {lib: work files, or
    None without a work payload}, {path: md5} for all those files)."""
    installed = {lib: sorted(p for p in (workdir / "MCNP_DATA" / lib).rglob("*") if p.is_file()) for lib in libs}
    payloads = {lib: workdir / "work" / lib / "payload" / lib for lib in libs}
    work_files = {
        lib: sorted(p for p in payloads[lib].rglob("*") if p.is_file()) if payloads[lib].is_dir() else None
        for lib in libs
    }
    todo = [p for lib in libs for p in installed[lib] + (work_files[lib] or [])]
    print(f"hashing {len(todo)} installed/work files", flush=True)
    with multiprocessing.Pool(jobs) as pool:
        digests = dict(pool.imap_unordered(hash_file_job, todo, chunksize=16))
    return installed, payloads, work_files, digests


def check_library(lib, lib_root, installed, payload, work_files, digests, by_name, by_md5):
    """(outcome counter, csv rows, used (archive, member) pairs, problems,
    warnings) for one library's installed files."""
    counter, rows, used, problems, warnings = Counter(), [], set(), [], []
    for path in installed:
        rel = path.relative_to(lib_root).as_posix()
        digest = digests[path]
        outcome, member, note = classify(lib, path.name, digest, by_name, by_md5)
        counter[outcome] += 1
        if member:
            used.add((member.archive, member.name))
            used.update((m.archive, m.name) for m in by_md5.get(digest, []))
        work_digest = ""
        if work_files is not None:
            work_digest = digests.get(payload / rel, "")
            if work_digest != digest:
                counter[WORK_DIFFERS] += 1
                state = "differs" if work_digest else "is missing"
                problems.append(f"{lib}/{rel}: work copy {state} from the installed file")
        if outcome in (DIFFERS, NOT_FOUND):
            problems.append(f"{lib}/{rel}: {outcome} {note}".rstrip())
        rows.append([lib, rel, digest, work_digest, outcome,
                     member.archive if member else "", member.name if member else "", note])
    if work_files is not None:
        extra = {p.relative_to(payload).as_posix() for p in work_files} - {
            p.relative_to(lib_root).as_posix() for p in installed
        }
        if extra:
            warnings.append(f"{lib}: {len(extra)} file(s) in work but not installed, e.g. {min(extra)}")
    return counter, rows, used, problems, warnings


def unused_members(archive_members, used):
    """Archive members that no installed file corresponds to, by library group and extension."""
    unused = defaultdict(list)
    for member in archive_members:
        if (member.archive, member.name) not in used:
            unused[(member.group, Path(member.name).suffix or "(none)")].append(member.name)
    return unused


def name_conflicts(by_name):
    """Same base name, different content, within one archive group (e.g. Cu65.ace in two zips)."""
    conflicts = []
    for base, members in sorted(by_name.items()):
        for group in sorted({m.group for m in members}):
            in_group = [m for m in members if m.group == group]
            if len({m.md5 for m in in_group}) > 1:
                conflicts.append((group, base, "; ".join(f"{m.archive}:{m.name} ({m.md5[:8]})" for m in in_group)))
    return conflicts


def count_warnings(libs, oracle, counts):
    warnings = []
    for lib in libs:
        want = oracle["libraries"].get(lib, {}).get("expected", {})
        for key, observed in (
            ("ace_files", sum(counts[lib][o] for o in OUTCOMES)),
            ("md5_renamed", counts[lib][MATCH_RENAMED]),
        ):
            if key in want and want[key] != observed:
                warnings.append(f"{lib}: {key} is {observed}, the oracle expects {want[key]}")
    return warnings


def summary_table(libs, counts, unused):
    """(header, rows with a final total row, total file count)."""
    header = ["Library", "Files", *OUTCOMES, WORK_DIFFERS, "Archive members unused"]
    table, totals = [], Counter()
    for lib in libs:
        counter = counts[lib]
        files = sum(counter[o] for o in OUTCOMES)
        table.append([lib, files, *(counter[o] for o in OUTCOMES), counter[WORK_DIFFERS], ""])
        totals.update(counter)
        totals["files"] += files
    table.append(["**Total**", totals["files"], *(totals[o] for o in OUTCOMES), totals[WORK_DIFFERS],
                  sum(len(v) for v in unused.values())])
    return header, table, totals["files"]


def write_report(path, summary, problems, warnings, unused, conflicts):
    """summary: the report's opening sections, before the problems."""
    report = ["# md5 validation: installed ACE files vs vendor archives\n", *summary]
    if problems:
        report.append(md_list("Problems", problems, REPORT_MAX))
    if warnings:
        report.append(md_list("Warnings", warnings))
    report.append("## Archive members with no installed counterpart\n\n" + md_table(
        ["Archive group", "Extension", "Count", "Examples"],
        [(g, e, len(m), ", ".join(sorted(m)[:3])) for (g, e), m in sorted(unused.items())],
    ))
    if conflicts:
        report.append("## Same name, different content in one archive group\n\n" + md_table(
            ["Group", "Name", "Archive:member (md5)"], conflicts))
    path.write_text("\n".join(report), encoding="utf8")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_common_args(parser)
    parser.add_argument("--jobs", type=int, default=4, help="Parallel workers for hashing (default 4).")
    args = parser.parse_args()
    workdir, out = resolve_paths(args)
    libs = installed_libraries(workdir, args.only)
    oracle = json.loads(Path(args.expected).read_text(encoding="utf8"))

    archive_members, problems, archive_count = hash_all_archives(workdir, args.jobs)
    by_name, by_md5 = index_members(archive_members)
    installed, payloads, work_files, digests = hash_installed(workdir, libs, args.jobs)

    csv_rows, counts, used, warnings = [], {}, set(), []
    problems += missing_libraries(libs, oracle, args.only)
    for lib in libs:
        counts[lib], rows, lib_used, lib_problems, lib_warnings = check_library(
            lib, workdir / "MCNP_DATA" / lib, installed[lib], payloads[lib], work_files[lib],
            digests, by_name, by_md5,
        )
        csv_rows += rows
        used |= lib_used
        problems += lib_problems
        warnings += lib_warnings
    warnings += count_warnings(libs, oracle, counts)
    unused = unused_members(archive_members, used)

    write_csv(
        out / "ace_origin_check.csv",
        ["library", "file", "installed_md5", "work_md5", "outcome", "archive", "member", "note"],
        csv_rows,
    )
    header, table, total_files = summary_table(libs, counts, unused)
    summary = [
        f"{len(archive_members)} archive members from {archive_count} archives; {len(digests)} files hashed.\n",
        md_table(header, table),
    ]
    skipped = [lib for lib in libs if work_files[lib] is None]
    if skipped:
        summary.append(f"work/ payload not present (skipped the work comparison): {', '.join(skipped)}\n")
    report_path = out / "md5_report.md"
    write_report(report_path, summary, problems, warnings, unused, name_conflicts(by_name))

    print(md_table(header[:6], [row[:6] for row in table]))
    return finish(problems, warnings, report_path, f"{total_files} installed files match the vendor archives")


if __name__ == "__main__":
    sys.exit(main())
