# SPDX-FileCopyrightText: 2026 ENEA
# SPDX-FileContributor: Alberto P
#
# SPDX-License-Identifier: MPL-2.0

"""Command-line interface for nd-prepare: download and repackage external nuclear
data libraries into nd_manager local-repository format.

See sources.json for the per-library manifest this reads, and README.md for
usage notes and known caveats.
"""

import argparse
import subprocess
import sys
from pathlib import Path

from . import __version__, prepare
from .common import PACKAGE_DIR, log
from .manifest import (
    format_manifest_listing,
    load_manifest,
    selected_entries,
    validate_only,
)


def build_parser():
    parser = argparse.ArgumentParser(prog="nd-prepare", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--manifest", default=str(PACKAGE_DIR / "sources.json"),
        help="Path to the sources.json manifest to read (default: the copy bundled with the package).",
    )
    parser.add_argument(
        "--workdir", default=None,
        help="Scratch directory for extraction (default: ./work).",
    )
    parser.add_argument(
        "--only", nargs="+", default=None,
        help="Only process these library keys (e.g. FENDL31D). Suffix a key with :neutron, :tsl, "
        ":proton, or :dosimetry to process just one part.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Process every library in the manifest (required when --only is not given).",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List the library keys and categories in the manifest (the values --only accepts), then exit.",
    )
    parser.add_argument(
        "--download-only", action="store_true",
        help="Only download the selected sources into cache/, then exit. A later run without "
        "this flag builds purely from cache.",
    )
    parser.add_argument(
        "--cleanup", action="store_true",
        help="After each entry is packed and hashed, delete its extracted payload and cached "
        "downloads (default: keep them so re-runs are cheap).",
    )
    parser.add_argument(
        "--cleanup-work", action="store_true",
        help="After each entry is packed and hashed, delete only its extracted payload under the "
        "workdir, keeping the cached downloads (a subset of --cleanup).",
    )
    parser.add_argument(
        "--compression", choices=["gz", "xz"], default="gz",
        help="Archive compression: gz (default) is faster, xz smaller.",
    )
    parser.add_argument(
        "--prune", action="store_true",
        help="Remove the entries in output/libraries.json that the manifest no longer produces, "
        "and delete their archives from output/, then exit.",
    )
    return parser


def run_entries(args, manifest, settings, libraries_json, stale):
    only = None
    if args.only:
        only = set(args.only)
        validate_only(only, manifest)

    handled = []
    names_by_entry = {}
    for entry_key, spec in selected_entries(manifest, only):
        handled.append(entry_key)
        if args.download_only:
            prepare.download_entry(entry_key, spec, settings.cache_dir)
        else:
            names_by_entry[entry_key] = prepare.process_entry(entry_key, spec, settings, libraries_json)
            prepare.save_output_libraries_json(settings.libraries_json_path, libraries_json)

    if not handled:
        log("nothing selected; no entries processed")
    elif args.download_only:
        log(f"download-only: sources for selected entries are cached under {settings.cache_dir}")
    else:
        log(f"libraries.json written to {settings.libraries_json_path}")

    prepare.report_table_name_collisions(names_by_entry)
    if stale and not args.download_only:
        prepare.warn_stale_entries(stale)


def run():
    parser = build_parser()
    args = parser.parse_args()
    if args.only and args.all:
        parser.error("--only and --all are mutually exclusive")
    if not (args.only or args.all or args.list or args.prune):
        parser.error("nothing selected: use --only KEY..., --all, --list or --prune")
    manifest = load_manifest(Path(args.manifest).resolve())

    if args.list:
        print(format_manifest_listing(manifest))
        return

    # Run artifacts go under the current directory, not next to the (installed)
    # manifest; local "path" sources are also resolved against it.
    settings = prepare.RunSettings(
        cache_dir=Path.cwd() / manifest["cache_dir"],
        output_dir=Path.cwd() / manifest["output_dir"],
        workdir=Path(args.workdir).resolve() if args.workdir else Path.cwd() / "work",
        compression=args.compression,
        cleanup=args.cleanup,
        cleanup_work=args.cleanup_work,
    )
    libraries_json = prepare.load_output_libraries_json(settings.libraries_json_path)
    stale = prepare.stale_library_keys(libraries_json, manifest)

    if args.prune:
        if stale:
            prepare.prune_stale_entries(libraries_json, stale, settings.output_dir)
            prepare.save_output_libraries_json(settings.libraries_json_path, libraries_json)
        else:
            log("prune: libraries.json has no stale entries")
        return

    run_entries(args, manifest, settings, libraries_json, stale)


def main():
    # Expected failures exit with a one-line message instead of a traceback.
    try:
        run()
    except subprocess.SubprocessError as exc:
        sys.exit(f"download failed: {exc}")
    except (RuntimeError, OSError) as exc:
        sys.exit(f"error: {exc}")
    except KeyboardInterrupt:
        sys.exit(130)
