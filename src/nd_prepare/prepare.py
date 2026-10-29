# SPDX-FileCopyrightText: 2026 ENEA
# SPDX-FileContributor: Alberto P
#
# SPDX-License-Identifier: MPL-2.0

"""Build each manifest entry into a packed archive and record it in libraries.json.

Per entry: build the payload (builders.py), check its xsdir, pack it
reproducibly (pack.py), hash it, and update output/libraries.json, which is
then ready to register with::

    nd_manager config --add-repo ExternalND output/libraries.json
"""

import json
import os
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .builders import build_payload
from .common import date_to_epoch, log, log_wrapped, plural_entries
from .manifest import entry_key, load_json_file, published_categories
from .pack import pack_payload
from .sources import resolve_archive_source, sha512_of_file
from .xsdir import check_xsdir_ascii, xsdir_table_names


@dataclass(frozen=True)
class RunSettings:
    """Where a run reads and writes, and how it packs and cleans up."""

    cache_dir: Path
    output_dir: Path
    workdir: Path
    compression: str = "gz"
    cleanup: bool = False  # after each entry, delete its payload and cached downloads
    cleanup_work: bool = False  # after each entry, delete only its payload

    @property
    def libraries_json_path(self):
        return self.output_dir / "libraries.json"


def iter_archive_sources(spec):
    """Yield every archive/xsdir_source/xsdir_overrides/patch spec a category
    needs to fetch, whatever its mode (used by --download-only)."""
    yield from spec.get("archives", [])
    if "xsdir_source" in spec:
        yield spec["xsdir_source"]
    if "xsdir_overrides" in spec:
        yield spec["xsdir_overrides"]
    yield from spec.get("patches", [])


def download_entry(key, spec, cache_dir):
    log(f"=== {key}: {spec['display_name']} (download only) ===")
    entry_cache_dir = cache_dir / key
    for archive in iter_archive_sources(spec):
        resolve_archive_source(archive, entry_cache_dir)


def process_entry(key, spec, settings, libraries_json):
    log(f"=== {key}: {spec['display_name']} ===")
    payload_dir = settings.workdir / key / "payload"
    if payload_dir.exists():
        shutil.rmtree(payload_dir)
    payload_dir.mkdir(parents=True)

    entry_cache_dir = settings.cache_dir / key
    build_payload(spec, entry_cache_dir, payload_dir, key)
    check_xsdir_ascii(payload_dir / "xsdir")
    table_names = xsdir_table_names(payload_dir / "xsdir")
    # Usually a vendor layout change or a wrong flatten_dirs: every entry was
    # dropped, and an empty library must not be published.
    if not table_names:
        raise RuntimeError(f"{key}: the xsdir has no entries left; check the manifest against the archive layout")

    archive_name = f"{key}.tar.{settings.compression}"
    output_archive = settings.output_dir / archive_name
    log(f"packing {key} -> {output_archive}")
    pack_payload(payload_dir, key, output_archive, settings.compression, date_to_epoch(spec["date"]))

    log(f"hashing {archive_name}")
    digest = sha512_of_file(output_archive)

    libraries_json.setdefault("ACE Libraries", {})[key] = {
        "date": spec["date"],
        "production": spec["production"],
        "description": spec["description"],
        "files": [
            {
                "name": archive_name,
                "path": archive_name,
                "sha512": digest,
            }
        ],
    }
    log(f"done: {key} (sha512={digest[:16]}...)")

    if settings.cleanup or settings.cleanup_work:
        shutil.rmtree(payload_dir, ignore_errors=True)
    if settings.cleanup:
        shutil.rmtree(entry_cache_dir, ignore_errors=True)

    return table_names


def report_table_name_collisions(names_by_entry):
    """Warn about table names defined more than once, within or across the
    processed libraries. Installing such libraries together gives the merged
    xsdir duplicate entries, and MCNP uses the first one. Informational only.
    """
    owners = {}
    for key, names in names_by_entry.items():
        for name in names:
            owners.setdefault(name, []).append(key)
    by_libraries = {}
    for name, keys in sorted(owners.items()):
        if len(keys) > 1:
            by_libraries.setdefault(tuple(dict.fromkeys(keys)), []).append(name)
    if not by_libraries:
        return
    count = sum(len(names) for names in by_libraries.values())
    log(f"warning: {count} table name(s) are defined more than once:")
    for keys, names in by_libraries.items():
        log(f"    {' + '.join(keys)} ({len(names)}):")
        log_wrapped(names, indent="        ")
    log("    installing these together gives duplicate xsdir entries; MCNP uses the first match")


def load_output_libraries_json(path):
    if not path.exists():
        return {"version": "1.0.0", "ACE Libraries": {}}
    data = load_json_file(path)
    # Checked here, as a hand-edited file would otherwise fail midway through a run.
    libraries = data.get("ACE Libraries", {}) if isinstance(data, dict) else None
    if not (isinstance(libraries, dict) and all(isinstance(entry, dict) for entry in libraries.values())):
        raise RuntimeError(f"{path}: expected an object whose \"ACE Libraries\" maps keys to objects")
    return data


def save_output_libraries_json(path, data):
    # Temp file plus rename: a crash cannot leave a truncated libraries.json,
    # which every later run reloads.
    data["date"] = date.today().isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def stale_library_keys(libraries_json, manifest):
    """Keys in libraries.json that this manifest no longer produces."""
    produced = {
        entry_key(key, category)
        for key, entry in manifest["libraries"].items()
        for category in published_categories(entry)
    }
    return sorted(set(libraries_json.get("ACE Libraries", {})) - produced)


def prune_stale_entries(libraries_json, stale, output_dir):
    libraries = libraries_json["ACE Libraries"]
    for key in stale:
        for file_entry in libraries.pop(key).get("files", []):
            archive = (output_dir / file_entry.get("path", "")).resolve()
            # Only delete a plain file directly in output/: a hand-added entry may point elsewhere.
            if archive.parent == output_dir.resolve() and archive.is_file() and archive.name != "libraries.json":
                archive.unlink()
                log(f"prune: deleted {archive.name}")
        log(f"prune: removed {key} from libraries.json")


def warn_stale_entries(stale):
    log(f"warning: libraries.json has {len(stale)} {plural_entries(len(stale))} this manifest no longer produces:")
    log_wrapped(stale)
    log("    nd_manager still offers them: run nd-prepare --prune to remove them and their archives")
