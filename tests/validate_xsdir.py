#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ENEA
# SPDX-FileContributor: Alberto P
#
# SPDX-License-Identifier: MPL-2.0
"""Independent xsdir check: the installed xsdirs against the vendor originals
and against the ACE headers.

For each library under <workdir>/MCNP_DATA/:

  1. Original vs final. The vendor xsdir text named in validation_expected.json
     is read straight from the archives in <workdir>. Entries are matched on
     (data file base name, address). awr, ftype, address, length, record length,
     entries, temperature and ptable must be identical text. The file field may
     change only to "<LIB>/<base name>[.ace]"; the route only from a placeholder
     ("iaea", "route") to "0"; the table name only when the oracle enables
     "rename_duplicated_names" and the original name was shared by several
     entries. Anything else is a MISMATCH. When several original sources list
     the same file (a stale and a corrected xsdir), the best match wins and
     the entry is counted as "superseded" if it is not the first source.
  2. Dropped entries. An original entry missing from the final xsdir is fine
     if its data file is not installed in that library; it is an error if it is.
  3. ACE headers. For every type-1 entry the 12 header lines at its address
     give name, AWR, temperature and NXS(1); they must agree with the entry.
  4. MCNP_DATA/xsdir_all must hold exactly the per-library entries.
  5. Counts are compared with the oracle (warning, or error with --strict).
  6. Every library of the oracle must be installed (not checked with --only).
  7. Line width. No line of xsdir_<lib> or of the directory section of xsdir_all
     may exceed 80 characters, "+" may only be the last token of a line, and
     the last line of a file may not continue.

Exits 1 on any MISMATCH or error.

Usage: python3 validate_xsdir.py [--workdir DIR] [--only LIB ...] [--strict]
                                 [--detail] [--write-expected-counts]
Reports: <out>/xsdir_summary.md (and with --detail the CSVs).
"""

import argparse
import json
import sys
from collections import Counter, defaultdict, namedtuple
from dataclasses import dataclass, field
from pathlib import Path

from validation_common import (
    FIELDS,
    REPORT_MAX,
    XSDIR_ENCODING,
    add_common_args,
    directory_lines,
    finish,
    installed_libraries,
    md_list,
    md_table,
    missing_libraries,
    parse_xsdir,
    read_source_text,
    resolve_paths,
    write_csv,
)

PLACEHOLDER_ROUTES = ("iaea", "route")
AWR_REL_TOL = 5e-4
TEMP_REL_TOL = 5e-3
ACE_HEADER_LINES = 12
MAX_SHOWN = 20
MAX_EXAMPLES = 5
MAX_XSDIR_LINE = 80
COUNT_KEYS = (
    "xsdir_entries", "original_entries", "matched", "identical", "modified", "renamed", "superseded",
    "only_in_original", "orphan_ace_files", "work_duplicate_names", "header_name_exceptions",
)

# One field that differs between an original and a final entry. status is
# "modified" or "renamed" (accepted) or "MISMATCH".
Diff = namedtuple("Diff", "field status note")
# The original entry a final entry was compared with, and how well it agreed.
Match = namedtuple("Match", "mismatches index orig diffs")


@dataclass
class LibraryResult:
    counts: Counter = field(default_factory=Counter)
    problems: list = field(default_factory=list)
    final: list = field(default_factory=list)  # parsed entries of xsdir_<lib>
    detail_rows: list = field(default_factory=list)
    sources: list = field(default_factory=list)  # (label, entry count) per original source
    superseded: list = field(default_factory=list)
    dropped: list = field(default_factory=list)
    orphans: list = field(default_factory=list)


def base_name(path):
    return Path(path.replace("\\", "/")).name


def entry_key(entry, ignore_case=False):
    base = base_name(entry["file"])
    if ignore_case:
        base = base.lower()
    return (base.removesuffix(".ace"), entry["addr"])


def compare_fields(orig, final, lib, rename_ok, ignore_case=False):
    """[Diff] for the fields that differ between an original and a final entry."""
    diffs = []
    for name in (*FIELDS, "extra"):
        o, f = orig.get(name, ""), final.get(name, "")
        if o == f:
            continue
        if name == "file":
            expected = (f"{lib}/{base_name(o)}", f"{lib}/{base_name(o)}.ace")
            ok = f.lower() in [e.lower() for e in expected] if ignore_case else f in expected
        elif name == "route":
            ok = o in PLACEHOLDER_ROUTES and f == "0"
        elif name == "name" and rename_ok:
            diffs.append(Diff(name, "renamed", f"{o} -> {f}"))
            continue
        else:
            ok = False
        diffs.append(Diff(name, "modified" if ok else "MISMATCH", f"{o} -> {f}"))
    return diffs


def check_line_widths(label, text):
    """Problems for lines over MAX_XSDIR_LINE characters and for misplaced or
    dangling "+" continuation markers."""
    problems = []
    lines = text.splitlines()
    too_long = [(number, line) for number, line in enumerate(lines, 1) if len(line) > MAX_XSDIR_LINE]
    if too_long:
        examples = "; ".join(f"line {number}: {len(line)} chars" for number, line in too_long[:3])
        problems.append(
            f"{label}: {len(too_long)} line(s) longer than {MAX_XSDIR_LINE} characters "
            f"(longest {max(len(line) for _n, line in too_long)}), e.g. {examples}"
        )
    misplaced = [number for number, line in enumerate(lines, 1) if "+" in line.split()[:-1]]
    if misplaced:
        problems.append(f"{label}: '+' is not the last token on {len(misplaced)} line(s), e.g. line {misplaced[0]}")
    for number, line in enumerate(lines, 1):
        if line.split()[-1:] == ["+"] and (number == len(lines) or not lines[number].strip()):
            problems.append(f"{label}: line {number} continues onto nothing")
            break
    return problems


def read_ace_headers(path, line_numbers):
    """{start line: 12 header lines} for each requested 1-based start line."""
    wanted = sorted(set(line_numbers))
    found, buf, index = {}, [], 0
    with open(path, encoding=XSDIR_ENCODING, errors="replace") as handle:
        for number, line in enumerate(handle, 1):
            if index >= len(wanted):
                break
            if number < wanted[index]:
                continue
            buf.append(line)
            if number == wanted[index] + ACE_HEADER_LINES - 1:
                found[wanted[index]] = buf
                buf, index = [], index + 1
    return found


def to_float(text):
    try:
        return float(text)
    except ValueError:
        return None


def parse_ace_header(buf):
    """(table name, AWR, temperature in MeV, NXS(1)) from the 12 header lines;
    NXS(1) is None when it cannot be read."""
    try:
        length = int((buf[6] + buf[7]).split()[0])
    except (IndexError, ValueError):
        length = None
    return buf[0][:10].strip(), to_float(buf[0][10:22]), to_float(buf[0][22:34]), length


def close(a, b, tol):
    return a is not None and b is not None and (a == b or abs(a - b) <= tol * max(abs(a), abs(b)))


def read_originals(workdir, config):
    """[(label, entries, names shared by several entries)] in oracle order."""
    originals = []
    for source in config["sources"]:
        entries, _bad = parse_xsdir(read_source_text(workdir, source))
        shared = {name for name, n in Counter(e["name"] for e in entries).items() if n > 1}
        originals.append((source.get("label", source["path"]), entries, shared))
    return originals


def compare_with_originals(lib, config, originals, by_key, result, want_detail):
    """Step 1: compare each final entry with its best-matching original.
    Returns (keys of the final entries, set of (name, file) of renamed ones)."""
    ignore_case = bool(config.get("ignore_file_case"))
    rename_ok = bool(config.get("rename_duplicated_names"))
    final_keys, renamed_entries = set(), set()
    for entry in result.final:
        key = entry_key(entry, ignore_case)
        final_keys.add(key)
        candidates = by_key.get(key)
        if not candidates:
            result.problems.append(
                f"{lib}: {entry['name']} ({entry['file']}) is in the final xsdir but in no original")
            continue
        matches = []
        for index, orig, shared in candidates:
            diffs = compare_fields(orig, entry, lib, rename_ok and orig["name"] in shared, ignore_case)
            matches.append(Match(sum(d.status == "MISMATCH" for d in diffs), index, orig, diffs))
        best = min(matches, key=lambda m: (m.mismatches, m.index))
        result.counts["matched"] += 1
        first_source_bad = [m for m in matches if m.index == 0 and m.mismatches]
        if best.index != 0 and first_source_bad:
            result.counts["superseded"] += 1
            stale = "; ".join(f"{d.field} {d.note}" for d in first_source_bad[0].diffs if d.status == "MISMATCH")
            result.superseded.append(
                f"{entry['name']}: first source differs ({stale}), source {originals[best.index][0]} agrees"
            )
        if want_detail:
            by_field = {d.field: d for d in best.diffs}
            for name in FIELDS:
                diff = by_field.get(name)
                result.detail_rows.append([
                    lib, originals[best.index][0], entry["name"], name, best.orig[name], entry[name],
                    diff.status if diff else "identical", diff.note if diff else "",
                ])
        if best.mismatches:
            result.problems += [
                f"{lib}: {entry['name']} {d.field}: {d.note}" for d in best.diffs if d.status == "MISMATCH"
            ]
        elif any(d.status == "renamed" for d in best.diffs):
            result.counts["renamed"] += 1
            renamed_entries.add((entry["name"], entry["file"]))
        elif best.diffs:
            result.counts["modified"] += 1
        else:
            result.counts["identical"] += 1
    return final_keys, renamed_entries


def check_duplicate_names(lib, config, result):
    names = Counter(e["name"] for e in result.final)
    repeated = sum(n - 1 for n in names.values() if n > 1)
    result.counts["work_duplicate_names"] = repeated
    if config.get("rename_duplicated_names") and repeated:
        result.problems.append(f"{lib}: renames were enabled but {repeated} table name(s) are still repeated")


def check_dropped(lib, by_key, final_keys, installed_names, result):
    """Step 2: original entries absent from the final xsdir."""
    for key, candidates in by_key.items():
        if key in final_keys:
            continue
        result.counts["only_in_original"] += 1
        name = candidates[0][1]["name"]
        result.dropped.append(f"{name} ({key[0]})")
        if key[0] in installed_names:
            result.problems.append(f"{lib}: original entry {name} was dropped although {key[0]} is installed")


def check_ace_headers(workdir, lib, renamed_entries, result):
    """Step 3: every type-1 entry against the 12 header lines of its ACE file."""
    by_file = defaultdict(list)
    for entry in result.final:
        by_file[entry["file"]].append(entry)
    for rel, entries in by_file.items():
        path = workdir / "MCNP_DATA" / rel
        if not path.is_file():
            result.problems.append(f"{lib}: {entries[0]['name']} references missing file {rel}")
            continue
        typed = [e for e in entries if e["ftype"] == "1" and e["addr"].isdigit()]
        headers = read_ace_headers(path, [int(e["addr"]) for e in typed]) if typed else {}
        for entry in typed:
            buf = headers.get(int(entry["addr"]))
            if not buf:
                result.problems.append(f"{lib}: {entry['name']}: no ACE header at line {entry['addr']} of {rel}")
                continue
            name, awr, temp, length = parse_ace_header(buf)
            if name != entry["name"]:
                if (entry["name"], entry["file"]) in renamed_entries:
                    result.counts["header_name_exceptions"] += 1
                else:
                    result.problems.append(f"{lib}: {entry['name']}: ACE header names the table {name!r} ({rel})")
            if not close(to_float(entry["awr"]), awr, AWR_REL_TOL):
                result.problems.append(f"{lib}: {entry['name']}: AWR {entry['awr']} vs header {awr} ({rel})")
            t = to_float(entry["temp"])
            if not (close(t, temp, TEMP_REL_TOL) or (t == 0 and temp == 0)):
                result.problems.append(f"{lib}: {entry['name']}: temperature {entry['temp']} vs header {temp} ({rel})")
            if not (entry["length"].isdigit() and int(entry["length"]) == length):
                result.problems.append(
                    f"{lib}: {entry['name']}: length {entry['length']} vs header NXS(1) {length} ({rel})")


def validate_library(workdir, lib, config, want_detail):
    result = LibraryResult()
    lib_root = workdir / "MCNP_DATA" / lib
    final_path = workdir / "MCNP_DATA" / "database" / f"xsdir_{lib}"
    if not final_path.is_file():
        result.problems.append(f"{lib}: {final_path} not found")
        return result
    final_text = final_path.read_text(encoding=XSDIR_ENCODING)
    result.problems += check_line_widths(f"xsdir_{lib}", final_text)
    result.final, malformed = parse_xsdir(final_text)
    if malformed:
        result.problems.append(f"{lib}: {len(malformed)} malformed xsdir line(s), e.g. {malformed[0]!r}")

    originals = read_originals(workdir, config)
    by_key = defaultdict(list)
    for index, (_label, entries, shared) in enumerate(originals):
        for entry in entries:
            by_key[entry_key(entry, bool(config.get("ignore_file_case")))].append((index, entry, shared))
    result.sources = [(label, len(entries)) for label, entries, _shared in originals]
    result.counts.update(xsdir_entries=len(result.final), original_entries=len(by_key))

    final_keys, renamed_entries = compare_with_originals(lib, config, originals, by_key, result, want_detail)
    check_duplicate_names(lib, config, result)

    installed = [p for p in lib_root.rglob("*") if p.is_file()]
    installed_names = {p.name for p in installed} | {p.name.removesuffix(".ace") for p in installed}
    check_dropped(lib, by_key, final_keys, installed_names, result)
    check_ace_headers(workdir, lib, renamed_entries, result)

    referenced = {Path(e["file"]).name for e in result.final}
    result.orphans = sorted(p.name for p in installed if p.name not in referenced)
    result.counts["orphan_ace_files"] = len(result.orphans)
    return result


def check_xsdir_all(workdir, libs, finals):
    """Problems if xsdir_all does not hold exactly the per-library entries."""
    path = workdir / "MCNP_DATA" / "xsdir_all"
    if not path.is_file():
        return [f"{path} not found"]
    text = path.read_text(encoding=XSDIR_ENCODING)
    lines = directory_lines(text)
    problems = [] if lines is not None else ["xsdir_all has no 'directory' line"]
    body = "\n".join(text.splitlines() if lines is None else lines)
    problems += check_line_widths("xsdir_all", body)
    merged, _bad = parse_xsdir(body)
    for lib in libs:
        want = Counter(tuple(e.values()) for e in finals[lib])
        have = Counter(tuple(e.values()) for e in merged if e["file"].startswith(f"{lib}/"))
        if want != have:
            problems.append(
                f"xsdir_all vs xsdir_{lib}: {sum((want - have).values())} entry(ies) missing, "
                f"{sum((have - want).values())} unexpected"
            )
    return problems


SUMMARY_COLUMNS = (
    ("Final entries", "xsdir_entries"), ("Original entries", "original_entries"), ("Identical", "identical"),
    ("Modified (path/route)", "modified"), ("Renamed", "renamed"), ("Superseded", "superseded"),
    ("Dropped", "only_in_original"), ("Orphan files", "orphan_ace_files"),
    ("Header-name exceptions", "header_name_exceptions"),
)


def summary_table(libs, results):
    header = ["Library", *(title for title, _key in SUMMARY_COLUMNS)]
    rows = [[lib, *(results[lib].counts[key] for _title, key in SUMMARY_COLUMNS)] for lib in libs if lib in results]
    return header, rows


def library_notes(result):
    """Markdown bullets describing one library's sources and exceptions."""
    notes = [f"- sources: {', '.join(f'{label} ({n} entries)' for label, n in result.sources)}"]
    if result.superseded:
        notes.append(f"- entries where the first source is stale ({len(result.superseded)}): "
                     + "; ".join(result.superseded[:MAX_SHOWN]))
    if result.dropped:
        notes.append(f"- original entries without an installed data file ({len(result.dropped)}), e.g. "
                     + ", ".join(result.dropped[:MAX_EXAMPLES]))
    if result.orphans:
        notes.append(f"- installed files with no xsdir entry ({len(result.orphans)}), e.g. "
                     + ", ".join(result.orphans[:MAX_EXAMPLES]))
    return notes


def write_report(path, header, rows, results, problems, warnings):
    report = ["# xsdir validation\n", md_table(header, rows)]
    report += [f"## {lib}\n\n" + "\n".join(library_notes(result)) + "\n"
               for lib, result in results.items() if result.sources]
    if problems:
        report.append(md_list("Problems", problems, REPORT_MAX))
    if warnings:
        report.append(md_list("Warnings", warnings))
    path.write_text("\n".join(report), encoding="utf8")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_common_args(parser)
    parser.add_argument("--strict", action="store_true", help="Treat count differences from the oracle as errors.")
    parser.add_argument(
        "--detail", action="store_true", help="Also write the field-level CSV (about 12 MB for a full run)."
    )
    parser.add_argument(
        "--write-expected-counts", action="store_true",
        help="Rewrite this script's 'expected' counts in the oracle from this run (the sources, the rules and "
        "validate_md5.py's counts are kept).",
    )
    args = parser.parse_args()
    workdir, out = resolve_paths(args)
    libs = installed_libraries(workdir, args.only)
    oracle = json.loads(Path(args.expected).read_text(encoding="utf8"))
    configs = oracle["libraries"]

    results = {}
    problems, warnings = missing_libraries(libs, oracle, args.only), []
    for lib in libs:
        if lib not in configs:
            problems.append(f"{lib}: installed but has no entry in the oracle {args.expected}")
            continue
        print(f"checking {lib}", flush=True)
        result = results[lib] = validate_library(workdir, lib, configs[lib], args.detail)
        problems += result.problems
        want = configs[lib].get("expected", {})
        for key in COUNT_KEYS:
            if key in want and want[key] != result.counts[key]:
                message = f"{lib}: {key} is {result.counts[key]}, the oracle expects {want[key]}"
                (problems if args.strict else warnings).append(message)
    if not args.only:
        problems += check_xsdir_all(workdir, list(results), {lib: r.final for lib, r in results.items()})

    if args.detail:
        write_csv(out / "xsdir_field_detail.csv",
                  ["library", "source", "entry", "field", "original", "final", "status", "note"],
                  [row for result in results.values() for row in result.detail_rows])

    if args.write_expected_counts:
        for lib, result in results.items():
            # Update, not replace: validate_md5.py keeps its own counts here too.
            configs[lib].setdefault("expected", {}).update({key: result.counts[key] for key in COUNT_KEYS})
        Path(args.expected).write_text(json.dumps(oracle, indent=2) + "\n", encoding="utf8")
        print(f"wrote expected counts to {args.expected}")

    header, rows = summary_table(libs, results)
    report_path = out / "xsdir_summary.md"
    write_report(report_path, header, rows, results, problems, warnings)

    print(md_table(header, rows))
    total = sum(r.counts["xsdir_entries"] for r in results.values())
    return finish(problems, warnings, report_path,
                  f"{total} xsdir entries in {len(results)} libraries are consistent with the originals "
                  "and the ACE headers")


if __name__ == "__main__":
    sys.exit(main())
