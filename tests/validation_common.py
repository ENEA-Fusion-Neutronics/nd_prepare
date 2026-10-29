# SPDX-FileCopyrightText: 2026 ENEA
# SPDX-FileContributor: Alberto P
#
# SPDX-License-Identifier: MPL-2.0
"""Helpers shared by the scripts in this directory.

Everything here uses only the Python standard library and never imports
nd_prepare or nd_manager. What the checks expect comes from the vendor archives
found in the workdir and from the hand-maintained validation_expected.json.
"""

import csv
import fnmatch
import hashlib
import re
import tarfile
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ORACLE = SCRIPT_DIR / "validation_expected.json"
ARCHIVE_SUFFIXES = (".zip", ".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".tar")
CHUNK = 4 << 20
# Directories of MCNP_DATA that are not libraries.
NON_LIBRARY_DIRS = ("database", "docs")
XSDIR_ENCODING = "latin-1"  # maps every byte to one character: never fails to decode
REPORT_MAX = 300  # problems listed in a markdown report
CONSOLE_MAX = 50  # problems printed on the terminal

# Positional fields of an xsdir entry (LA-UR-25-26264). The first
# seven are required; the record length, entries per record, temperature and
# "ptable" flag may be left out, and then read as empty.
FIELDS = ("name", "awr", "file", "route", "ftype", "addr", "length", "reclen", "nent", "temp", "ptable")
MIN_ENTRY_FIELDS = 7
TABLE_NAME_RE = re.compile(r"^\S+\.\d{2,3}[a-zA-Z]$")


def add_common_args(parser):
    parser.add_argument(
        "--workdir", default=".",
        help="Directory holding cache/, work/ and MCNP_DATA/ (the --workdir of verify_full_run.py), "
        "plus any local-path source zips. Default: current directory.",
    )
    parser.add_argument(
        "--only", nargs="+", default=None, metavar="LIB",
        help="Only validate these libraries, e.g. IRDFF2_DOSIMETRY (default: every library under MCNP_DATA).",
    )
    parser.add_argument(
        "--expected", default=str(DEFAULT_ORACLE),
        help=f"Oracle JSON (default: {DEFAULT_ORACLE}).",
    )
    parser.add_argument(
        "--out", default=None,
        help="Report directory (default: <workdir>/validation).",
    )


def resolve_paths(args):
    workdir = Path(args.workdir).resolve()
    out = Path(args.out).resolve() if args.out else workdir / "validation"
    out.mkdir(parents=True, exist_ok=True)
    return workdir, out


def installed_libraries(workdir, only=None):
    root = workdir / "MCNP_DATA"
    if not root.is_dir():
        raise SystemExit(f"error: {root} not found: pass --workdir")
    libs = sorted(p.name for p in root.iterdir() if p.is_dir() and p.name not in NON_LIBRARY_DIRS)
    if only:
        unknown = sorted(set(only) - set(libs))
        if unknown:
            raise SystemExit(f"error: not installed under MCNP_DATA: {', '.join(unknown)}")
        libs = [lib for lib in libs if lib in only]
    return libs


def missing_libraries(libs, oracle, only=None):
    """Problems for oracle libraries that are not installed under MCNP_DATA.
    With --only the selection is deliberate, so nothing is reported."""
    if only:
        return []
    return [
        f"{lib}: listed in the oracle but not installed under MCNP_DATA"
        for lib in sorted(set(oracle["libraries"]) - set(libs))
    ]


# ---------------------------------------------------------------- archives and md5

def md5_stream(handle):
    """(md5 hex digest, size) of a binary file object, read in chunks."""
    digest = hashlib.md5(usedforsecurity=False)
    size = 0
    for chunk in iter(lambda: handle.read(CHUNK), b""):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def iter_members(path):
    """(name, file object) for every file member of a zip or tar archive,
    streamed forward-only: nothing is written to disk."""
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if not info.is_dir():
                    with archive.open(info) as handle:
                        yield info.filename, handle
    else:
        with tarfile.open(path, "r|*") as archive:
            for info in archive:
                if info.isfile():
                    yield info.name, archive.extractfile(info)


def hash_archive(path):
    """[(member, size, md5)] for every file member."""
    members = []
    for name, handle in iter_members(path):
        digest, size = md5_stream(handle)
        members.append((name, size, digest))
    return members


def find_archives(workdir):
    """Every vendor archive the workdir holds: cache/<LIB>/* and zips placed
    directly in the workdir (local-path sources). Returns (group, path) pairs;
    group is the cache subdirectory name, or "(workdir)"."""
    found = []
    cache = workdir / "cache"
    if cache.is_dir():
        for lib_dir in sorted(p for p in cache.iterdir() if p.is_dir()):
            found += [(lib_dir.name, p) for p in sorted(lib_dir.iterdir()) if p.name.endswith(ARCHIVE_SUFFIXES)]
    found += [("(workdir)", p) for p in sorted(workdir.iterdir()) if p.is_file() and p.name.endswith(ARCHIVE_SUFFIXES)]
    return found


# ---------------------------------------------------------------- xsdir

def iter_records(lines):
    """Token list of each logical xsdir record; a line ending in "+" continues
    onto the next one."""
    tokens = []
    for line in lines:
        parts = line.split()
        continued = parts[-1:] == ["+"]
        tokens += parts[:-1] if continued else parts
        if not continued:
            yield tokens
            tokens = []
    if tokens:
        yield tokens


def parse_xsdir(text):
    """(entries, malformed): entries are dicts keyed by FIELDS plus "extra".
    Prose and header lines (first token not a table name) are skipped."""
    entries, malformed = [], []
    for tokens in iter_records(text.splitlines()):
        if not tokens or not TABLE_NAME_RE.match(tokens[0]):
            continue
        if len(tokens) < MIN_ENTRY_FIELDS:
            malformed.append(" ".join(tokens))
            continue
        entry = dict.fromkeys((*FIELDS, "extra"), "")
        entry.update(zip(FIELDS, tokens))
        entry["extra"] = " ".join(tokens[len(FIELDS):])
        entries.append(entry)
    return entries, malformed


def directory_lines(text):
    """Lines after the literal "directory" marker of a merged xsdir, or None
    if there is no marker."""
    lines = text.splitlines()
    for number, line in enumerate(lines):
        if line.strip().lower() == "directory":
            return lines[number + 1:]
    return None


def read_source_text(workdir, source):
    """Text of one oracle xsdir source: {"path": glob relative to the workdir,
    "member": fnmatch pattern inside an archive (omit for a loose file),
    "all_archives": true if the glob matches many archives (one fragment each),
    "placeholder_name": true to replace the token "filename" by <member stem>.ace}.
    Several matching members are concatenated in name order."""
    matches = sorted(workdir.glob(source["path"]))
    if len(matches) != 1 and not (matches and source.get("all_archives")):
        raise SystemExit(f"error: oracle source {source['path']!r} matched {len(matches)} files in {workdir}")
    if "member" not in source:
        return matches[0].read_text(encoding=XSDIR_ENCODING)
    texts = {}
    for path in matches:
        for name, handle in iter_members(path):
            if fnmatch.fnmatchcase(name, source["member"]):
                texts[(path.name, name)] = handle.read().decode(XSDIR_ENCODING)
    if not texts:
        raise SystemExit(f"error: no member matching {source['member']!r} in {matches[0]}")
    if source.get("placeholder_name"):
        texts = {key: text.replace("filename", f"{Path(key[1]).stem}.ace") for key, text in texts.items()}
    return "\n".join(texts[key] for key in sorted(texts))


# ---------------------------------------------------------------- reports

def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def md_table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def md_list(title, items, limit=None):
    return f"## {title}\n\n" + "\n".join(f"- {item}" for item in items[:limit]) + "\n"


def finish(problems, warnings, report_path, ok_message):
    """Print warnings and the verdict; return the exit code."""
    for warning in warnings:
        print(f"warning: {warning}")
    if problems:
        print(f"FAILED: {len(problems)} problem(s):")
        for problem in problems[:CONSOLE_MAX]:
            print(f"    {problem}")
        print(f"report: {report_path}")
        return 1
    print(f"PASSED: {ok_message}. report: {report_path}")
    return 0
