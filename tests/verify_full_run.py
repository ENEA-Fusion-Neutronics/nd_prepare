#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ENEA
# SPDX-FileContributor: Alberto P
#
# SPDX-License-Identifier: MPL-2.0
"""Full-download verification harness for nd_prepare + nd_manager.

Runs the ENTIRE sources.json manifest through nd-prepare (about 24-26 GB, see
the README's "Size and licensing"), installs every archive into a throwaway
nd_manager profile, generates a merged xsdir, and verifies:

  1. Layout: every file nd_manager installed for a library lives under
     <data path>/<KEY>/..., as nest_payload_data() (builders.py) produces.
  2. xsdir correctness: every entry in each per-library xsdir_<KEY> file and
     in the merged xsdir_all references a file that exists on disk.

Your real ~/.config/lanl-nd-manager profile is not touched. The throwaway
profile's only repository is a local path to this run's output/libraries.json
(see write_scratch_nd_manager_config) and its "code support" list is empty, so
nd_manager never uses the network. Re-running is safe: the cache under
--workdir means only missing downloads are fetched.

D1SDATA is not published online (see the README's "path" sources): its three
source zips are looked for in --workdir, and if any is missing they are listed
and D1SDATA is skipped.

Usage:
    python3 verify_full_run.py [--workdir DIR] [--compression {gz,xz}]
                                [--nd-prepare-path DIR] [--nd-manager-path DIR]
                                [--only KEY[:CATEGORY] ...] [--cleanup] [--cleanup-work]

Disk space: at least 60 GB free under --workdir for a full run (downloads,
repacked archives, and a temporary copy made during nd_manager install that is
deleted after each library, via "keep downloads": false). --cleanup-work lowers
this by deleting each entry's unpacked payload under work/ as soon as it is packed.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

from validation_common import MIN_ENTRY_FIELDS, XSDIR_ENCODING, iter_records

# This script lives in <nd_prepare>/tests; nd_manager is expected next to nd_prepare.
ND_PREPARE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ND_MANAGER_ROOT = Path(os.environ.get("ND_MANAGER_PATH", ND_PREPARE_ROOT.parent / "nd_manager"))
# Categories of a sources.json library entry that hold sources.
CATEGORIES = ("neutron", "tsl", "proton", "dosimetry")


def log(msg):
    print(f"[verify] {msg}", flush=True)


def run(cmd, **kwargs):
    log("+ " + " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, **kwargs)


def setup_venv(venv_dir, nd_prepare_root, nd_manager_root):
    pip = venv_dir / "bin" / "pip"
    if not (venv_dir / "bin" / "python3").exists():
        log(f"creating venv at {venv_dir}")
        venv.EnvBuilder(with_pip=True).create(venv_dir)
        # Only on a fresh venv: a pre-built venv copied to an offline host
        # (PIP_NO_INDEX=1, see run_verify_full.slurm) must re-run as a no-op,
        # and --upgrade would fail there. Hence --no-build-isolation below too.
        run([str(pip), "install", "-q", "--upgrade", "pip", "setuptools", "wheel"])
    run([str(pip), "install", "-q", "--no-build-isolation", "-e", str(nd_prepare_root)])
    run([str(pip), "install", "-q", "--no-build-isolation", "-e", str(nd_manager_root)])


def find_missing_local_sources(manifest_path, workdir):
    """Map each manifest key to the local "path" sources (D1SDATA's zips, today)
    that are missing from workdir, read from the manifest itself so this cannot
    drift from sources.json. Keys whose local sources are all present, or that
    have none, are left out.
    """
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    missing = {}
    for key, entry in manifest["libraries"].items():
        for category in CATEGORIES:
            spec = entry.get(category) or {}
            sources = [*spec.get("archives", []), *spec.get("patches", [])]
            sources += [spec[field] for field in ("xsdir_source", "xsdir_overrides") if field in spec]
            for source in sources:
                if "path" in source and not (workdir / source["path"]).exists():
                    missing.setdefault(key, []).append(source["path"])
    return missing


def manifest_keys(bin_dir, workdir):
    listing = subprocess.run(
        [str(bin_dir / "nd-prepare"), "--list"],
        cwd=workdir, check=True, capture_output=True, text=True,
    ).stdout
    return [line.split()[0] for line in listing.splitlines() if line.strip()]


def run_nd_prepare(bin_dir, workdir, compression, only_args, cleanup_work=False):
    cmd = [str(bin_dir / "nd-prepare"), "--compression", compression]
    if cleanup_work:
        cmd.append("--cleanup-work")
    if only_args:
        cmd += ["--only", *only_args]
    run(cmd, cwd=workdir)


def write_scratch_nd_manager_config(config_dir, data_path, download_path, libraries_json_path):
    profile = {
        "data path": str(data_path),
        "download path": str(download_path),
        "repositories": [
            {
                "name": "LocalPrep",
                "description": "This run's freshly built nd_prepare output, for verification.",
                "path": str(libraries_json_path),
            }
        ],
        "offline": False,
        "keep downloads": False,  # delete nd_manager's install-time copy right after each install
        "code support": [],  # avoid any network contact beyond nd-prepare's own downloads
    }
    config = {
        "version": "1.0.0",
        "profile": "verify",
        "profiles": {"verify": profile},
    }
    lanl_dir = config_dir / "lanl-nd-manager"
    lanl_dir.mkdir(parents=True, exist_ok=True)
    (lanl_dir / "config.json").write_text(json.dumps(config, indent=2), encoding="utf8")


def run_nd_manager(bin_dir, env, *args):
    run([str(bin_dir / "nd_manager"), *args, "-y"], env=env)


def check_xsdir_file(xsdir_path, data_path, label, failures, skip_until_directory=False):
    """Check that the file ends with a newline, has no blank or control-character
    lines, and that every entry has route 0 and a filename that resolves.

    Per-library xsdir_<KEY> files are pure entries with no header. The merged
    xsdir_all starts with an atomic weight table and a date line before a
    literal "directory" marker, and only what follows is real entries;
    skip_until_directory=True mirrors that so a stray header line (e.g. a
    comment with enough fields) cannot be mistaken for an entry.
    """
    raw = xsdir_path.read_bytes()
    lines = raw.decode(XSDIR_ENCODING).splitlines()
    blank = [
        i + 1 for i, line in enumerate(lines)
        if not any(ch.isprintable() and not ch.isspace() for ch in line)
    ]
    if blank:
        failures.append(f"{label}: blank or control-character line(s) at {blank[:5]}")
    if lines and not raw.endswith(b"\n"):
        failures.append(f"{label}: does not end with a newline")
    if skip_until_directory:
        markers = [i for i, line in enumerate(lines) if line.strip().lower() == "directory"]
        lines = lines[markers[0] + 1:] if markers else []
    for fields in iter_records(lines):
        if len(fields) < MIN_ENTRY_FIELDS:
            continue
        if fields[3] != "0":
            failures.append(f"{label}: {fields[0]} has access route {fields[3]!r}, expected '0'")
        referenced = data_path / fields[2]
        if not referenced.exists():
            failures.append(f"{label}: references missing file: {fields[2]}")


def verify_layout_and_xsdirs(data_path):
    """Problems for files installed outside their library and unresolved xsdir entries."""
    database = json.loads((data_path / "database" / "database.json").read_text(encoding="utf8"))
    failures = []
    for info in database.values():
        name = info["name"]
        prefix = f"{name}/"
        for rel in info.get("files", []):
            if not rel.startswith((prefix, "database/", "docs/")):
                failures.append(f"{name}: file installed outside <data path>/{name}/: {rel}")

        xsdir_rel = info.get("xsdir")
        if xsdir_rel:
            check_xsdir_file(data_path / xsdir_rel, data_path, f"xsdir_{name}", failures)

    merged_path = data_path / "xsdir_all"
    if merged_path.exists():
        check_xsdir_file(merged_path, data_path, "xsdir_all", failures, skip_until_directory=True)
    else:
        failures.append("merged xsdir_all was not generated")

    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--workdir", default="nd_prepare_full_verify",
        help="Scratch directory for everything: venv, nd-prepare's cache/output/work, and the "
        "throwaway nd_manager profile. Default: ./nd_prepare_full_verify",
    )
    parser.add_argument("--compression", choices=["gz", "xz"], default="gz")
    parser.add_argument(
        "--nd-prepare-path", default=str(ND_PREPARE_ROOT),
        help=f"Path to the nd_prepare package root. Default: {ND_PREPARE_ROOT} (assumes this "
        "script lives one directory below the nd_prepare checkout root: pass this explicitly "
        "when running the script from elsewhere, e.g. a cache directory).",
    )
    parser.add_argument(
        "--nd-manager-path", default=str(DEFAULT_ND_MANAGER_ROOT),
        help=f"Path to the nd_manager package root. Default: {DEFAULT_ND_MANAGER_ROOT} "
        "(the sibling checkout this script expects next to nd_prepare).",
    )
    parser.add_argument(
        "--cleanup", action="store_true",
        help="Delete --workdir at the end, but only if verification passed. Off by default so "
        "re-runs stay cheap (nd-prepare's cache/ is preserved) and results stay inspectable.",
    )
    parser.add_argument(
        "--cleanup-work", action="store_true",
        help="Forward --cleanup-work to nd-prepare: delete each entry's extracted payload under "
        "work/ as soon as it is packed (cache/ is kept). Lowers peak disk use during the run.",
    )
    parser.add_argument(
        "--only", nargs="+", default=None,
        help="Restrict to specific manifest keys (forwarded to nd-prepare's own --only, e.g. "
        "'IRDFF2:dosimetry'), instead of the whole manifest (minus D1SDATA only when its zips "
        "are missing from --workdir). Mainly for smoke-testing "
        "this harness itself on one small library before committing to the full ~24-26 GB run.",
    )
    args = parser.parse_args()

    nd_prepare_root = Path(args.nd_prepare_path).resolve()
    if not (nd_prepare_root / "pyproject.toml").exists():
        raise SystemExit(f"error: {nd_prepare_root} has no pyproject.toml: pass --nd-prepare-path")

    nd_manager_root = Path(args.nd_manager_path).resolve()
    if not (nd_manager_root / "pyproject.toml").exists():
        raise SystemExit(f"error: {nd_manager_root} has no pyproject.toml: pass --nd-manager-path")

    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    venv_dir = workdir / "venv"
    bin_dir = venv_dir / "bin"
    config_dir = workdir / "xdg_config"
    data_path = workdir / "MCNP_DATA"
    download_path = workdir / "Downloads"
    libraries_json_path = workdir / "output" / "libraries.json"

    setup_venv(venv_dir, nd_prepare_root, nd_manager_root)

    if args.only:
        only_args = list(args.only)
    else:
        manifest_path = nd_prepare_root / "src" / "nd_prepare" / "sources.json"
        missing_local = find_missing_local_sources(manifest_path, workdir)
        if missing_local:
            for key, names in missing_local.items():
                log(f"{key} source files not found in --workdir; skipping {key} this run:")
                for name in names:
                    log(f"    {name}")
            log(
                "Place them directly in --workdir and re-run to include them too "
                "(see nd_prepare/README.md's \"path\" source note)."
            )
            only_args = [k for k in manifest_keys(bin_dir, workdir) if k not in missing_local]
        else:
            only_args = []

    run_nd_prepare(bin_dir, workdir, args.compression, only_args, args.cleanup_work)

    write_scratch_nd_manager_config(config_dir, data_path, download_path, libraries_json_path)
    env = dict(os.environ, XDG_CONFIG_HOME=str(config_dir))

    run_nd_manager(bin_dir, env, "update")
    run_nd_manager(bin_dir, env, "download", "--all", "available")
    run_nd_manager(bin_dir, env, "install", "--all", "available")
    run_nd_manager(bin_dir, env, "create-xsdir", "--all", "available")

    failures = verify_layout_and_xsdirs(data_path)
    if failures:
        log(f"FAILED: {len(failures)} problem(s):")
        for failure in failures:
            log(f"    {failure}")
    else:
        log(
            "PASSED: every installed file is under its library's own directory, and every "
            "xsdir entry (per-library and merged) resolves to a real file."
        )

    if args.cleanup and not failures:
        log(f"--cleanup: removing {workdir}")
        shutil.rmtree(workdir)

    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:
        sys.exit(f"error: command failed ({exc.returncode}): {' '.join(str(c) for c in exc.cmd)}")
