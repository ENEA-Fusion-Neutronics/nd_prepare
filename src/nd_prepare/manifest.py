# SPDX-FileCopyrightText: 2026 ENEA
# SPDX-FileContributor: Alberto P
#
# SPDX-License-Identifier: MPL-2.0

"""Loading and validating the sources.json manifest."""

import json
import re
from pathlib import Path

from .common import CATEGORIES, date_to_epoch
from .sources import source_cache_name

ARCHIVE_KINDS = ("zip", "tar")

# Per build mode: the category fields it requires and the ones it accepts
# optionally, with their JSON types; "single_archive" modes take one archive.
MODES = {
    "ready": {
        "required": {"xsdir_member": str},
        "optional": {
            "flatten_dirs": list,
            "rename_members": dict,
            "discard_members": list,
            "rename_xsdir_tables": dict,
        },
        "single_archive": True,
    },
    "fragments": {
        "required": {"fragment_glob": str},
        "optional": {"flatten_dirs": list, "rename_xsdir_files": dict},
        "single_archive": False,
    },
    "merge-master": {
        "required": {"xsdir_source": dict},
        "optional": {"xsdir_overrides": dict, "strip_top_level_dir": bool},
        "single_archive": False,
    },
}
ALL_MODE_FIELDS = {
    field: expected
    for config in MODES.values()
    for part in ("required", "optional")
    for field, expected in config[part].items()
}
# Fields accepted by every mode.
COMMON_FIELD_TYPES = {"drop_members": list, "patches": list, "readme_member": str}
UNTYPED_COMMON_FIELDS = ("mode", "archives", "display_name", "description")
MANIFEST_FIELDS = ("_comment", "cache_dir", "output_dir", "libraries")
ENTRY_FIELDS = ("display_name", "date", "description", "production", *CATEGORIES)
SOURCE_FIELDS = ("url", "path", "kind", "md5", "cache_name")
PATCH_FILE_FIELDS = ("member", "dest", "xsdir_fragment")
# Fields naming paths inside the extracted payload: a single path, or a list.
PATH_FIELDS = ("xsdir_member", "readme_member", "fragment_glob")
PATH_LIST_FIELDS = ("flatten_dirs", "discard_members", "drop_members")
# A library key names directories and archives, and "_" and ":" separate it
# from the category in entry keys and --only values.
LIBRARY_KEY_RE = re.compile(r"[A-Za-z0-9]+")


def published_categories(entry):
    """The categories a library entry publishes (those not null or missing)."""
    return [category for category in CATEGORIES if entry.get(category) is not None]


def entry_key(key, category):
    """The libraries.json key of one category of a library, e.g. FENDL31D_NEUTRON."""
    return f"{key}_{category.upper()}"


def _check_fields(obj, allowed, where, errors):
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        errors.append(f"{where}: unknown field(s): {', '.join(repr(field) for field in unknown)}")


def _is_safe_relative_path(name):
    """True for a non-empty relative path with no ".." component."""
    path = Path(name)
    return bool(path.parts) and not path.is_absolute() and ".." not in path.parts


def _validate_source(source, where, errors, needs_kind=True, extra_fields=()):
    """Check one source (archive, xsdir_source, xsdir_overrides or patch); return
    its cache basename (url sources only) so the caller can spot collisions."""
    if not isinstance(source, dict):
        errors.append(f"{where}: must be an object")
        return None
    allowed = SOURCE_FIELDS if needs_kind else tuple(field for field in SOURCE_FIELDS if field != "kind")
    _check_fields(source, (*allowed, *extra_fields), where, errors)
    if ("url" in source) == ("path" in source):
        errors.append(f"{where}: needs exactly one of \"url\" or \"path\"")
    md5 = source.get("md5")
    if not (isinstance(md5, str) and len(md5) == 32 and all(c in "0123456789abcdef" for c in md5)):
        errors.append(f"{where}: \"md5\" must be a 32-character lowercase hex digest, got {md5!r}")
    if needs_kind and source.get("kind") not in ARCHIVE_KINDS:
        errors.append(f"{where}: \"kind\" must be one of {', '.join(ARCHIVE_KINDS)}, got {source.get('kind')!r}")
    cache_name = source.get("cache_name")
    if "cache_name" in source and not (
        isinstance(cache_name, str) and "/" not in cache_name and _is_safe_relative_path(cache_name)
    ):
        errors.append(f"{where}: \"cache_name\" must be a plain file name (no \"/\" or \"..\"), got {cache_name!r}")
    elif isinstance(source.get("url"), str):
        return source_cache_name(source)
    return None


def _validate_mode_fields(spec, where, errors):
    mode = spec.get("mode")
    if mode in MODES:
        applicable = {**MODES[mode]["required"], **MODES[mode]["optional"]}
        for field in MODES[mode]["required"]:
            if field not in spec:
                errors.append(f"{where}: mode {mode!r} requires \"{field}\"")
        for field in sorted(set(spec) - {*UNTYPED_COMMON_FIELDS, *COMMON_FIELD_TYPES, *applicable}):
            if field in ALL_MODE_FIELDS:
                errors.append(f"{where}: \"{field}\" does not apply to mode {mode!r}")
            else:
                errors.append(f"{where}: unknown field: {field!r}")
    else:
        errors.append(f"{where}: \"mode\" must be one of {', '.join(MODES)}, got {mode!r}")

    for field, expected in {**COMMON_FIELD_TYPES, **ALL_MODE_FIELDS}.items():
        if field in spec and not isinstance(spec[field], expected):
            errors.append(f"{where}: \"{field}\" must be a JSON {expected.__name__}")


def _validate_payload_paths(spec, where, errors):
    """Fields that name payload paths must stay inside the payload. Fields of
    the wrong JSON type are left to _validate_mode_fields."""
    for field in PATH_FIELDS:
        if isinstance(spec.get(field), str) and not _is_safe_relative_path(spec[field]):
            errors.append(f"{where}: \"{field}\" must be a relative path inside the payload, got {spec[field]!r}")
    for field in PATH_LIST_FIELDS:
        names = spec.get(field)
        if isinstance(names, list) and not all(
            isinstance(name, str) and _is_safe_relative_path(name) for name in names
        ):
            errors.append(f"{where}: \"{field}\" must list relative paths inside the payload, got {names!r}")
    renames = spec.get("rename_members")
    if isinstance(renames, dict) and not all(
        isinstance(value, str) and _is_safe_relative_path(name) and _is_safe_relative_path(value)
        for name, value in renames.items()
    ):
        errors.append(f"{where}: \"rename_members\" must map relative paths inside the payload to others")


def _validate_xsdir_renames(spec, where, errors):
    renames = spec.get("rename_xsdir_files")
    if isinstance(renames, dict) and not all(
        isinstance(key, str) and isinstance(value, str) for key, value in renames.items()
    ):
        errors.append(f"{where}: \"rename_xsdir_files\" must map file names to file names (strings)")

    renames = spec.get("rename_xsdir_tables")
    if isinstance(renames, dict):
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in renames.items()):
            errors.append(f"{where}: \"rename_xsdir_tables\" must map file names to table names (strings)")
        elif len(set(renames.values())) != len(renames):
            errors.append(f"{where}: \"rename_xsdir_tables\" maps several files to one table name")


def _validate_sources(spec, where, errors):
    """Validate every source of a category, and flag url sources whose cache
    file names collide (they share one cache directory)."""
    named = []
    mode = spec.get("mode")

    archives = spec.get("archives")
    if not isinstance(archives, list) or not archives:
        errors.append(f"{where}: \"archives\" must be a non-empty list")
        archives = []
    elif mode in MODES and MODES[mode]["single_archive"] and len(archives) != 1:
        errors.append(f"{where}: mode {mode!r} takes exactly one archive, got {len(archives)}")
    for index, archive in enumerate(archives):
        source_where = f"{where}.archives[{index}]"
        named.append((source_where, _validate_source(archive, source_where, errors)))

    if isinstance(spec.get("xsdir_source"), dict):
        source_where = f"{where}.xsdir_source"
        named.append((source_where, _validate_source(spec["xsdir_source"], source_where, errors, needs_kind=False)))

    if isinstance(spec.get("xsdir_overrides"), dict):
        source_where = f"{where}.xsdir_overrides"
        named.append((source_where, _validate_source(
            spec["xsdir_overrides"], source_where, errors, needs_kind=False, extra_fields=("entries",),
        )))
        entries = spec["xsdir_overrides"].get("entries")
        if not (isinstance(entries, list) and entries and all(isinstance(name, str) for name in entries)):
            errors.append(f"{source_where}: \"entries\" must be a non-empty list of table names")

    patches = spec.get("patches")
    for index, patch in enumerate(patches if isinstance(patches, list) else []):
        patch_where = f"{where}.patches[{index}]"
        named.append((patch_where, _validate_source(patch, patch_where, errors, extra_fields=("files",))))
        _validate_patch_files(patch, patch_where, errors)

    first_user = {}
    for source_where, name in named:
        if name is None:
            continue
        if name in first_user:
            errors.append(f"{source_where}: cache filename {name!r} collides with {first_user[name]}")
        else:
            first_user[name] = source_where


def _validate_patch_files(patch, patch_where, errors):
    files = patch.get("files") if isinstance(patch, dict) else None
    if not isinstance(files, list) or not files:
        errors.append(f"{patch_where}: \"files\" must be a non-empty list")
        return
    for index, file_spec in enumerate(files):
        file_where = f"{patch_where}.files[{index}]"
        if not (isinstance(file_spec, dict) and isinstance(file_spec.get("member"), str)
                and isinstance(file_spec.get("dest"), str)):
            errors.append(f"{file_where}: needs string \"member\" and \"dest\"")
            continue
        _check_fields(file_spec, PATCH_FILE_FIELDS, file_where, errors)
        if not _is_safe_relative_path(file_spec["dest"]):
            errors.append(
                f"{file_where}: \"dest\" must be a relative path inside the payload, got {file_spec['dest']!r}"
            )
        if not _is_safe_relative_path(file_spec["member"]):
            errors.append(
                f"{file_where}: \"member\" must be a relative path inside the patch, got {file_spec['member']!r}"
            )
        fragment = file_spec.get("xsdir_fragment")
        if "xsdir_fragment" in file_spec and not (isinstance(fragment, str) and _is_safe_relative_path(fragment)):
            errors.append(
                f"{file_where}: \"xsdir_fragment\" must be a relative path inside the patch, got {fragment!r}"
            )


def _validate_category(spec, where, category, errors):
    if not isinstance(spec, dict):
        errors.append(f"{where}: must be an object or null")
        return
    _validate_mode_fields(spec, where, errors)
    _validate_payload_paths(spec, where, errors)
    _validate_xsdir_renames(spec, where, errors)
    # neutron's display_name/description come from the entry; the others carry their own.
    if category != "neutron":
        for field in ("display_name", "description"):
            if not isinstance(spec.get(field), str):
                errors.append(f"{where}: \"{field}\" is required (a string) for a non-neutron category")
    _validate_sources(spec, where, errors)


def _validate_library(key, entry, errors):
    if not LIBRARY_KEY_RE.fullmatch(key):
        errors.append(f"libraries.{key}: the key must be letters and digits only, it names files and directories")
    if not isinstance(entry, dict):
        errors.append(f"libraries.{key}: must be an object")
        return
    _check_fields(entry, ENTRY_FIELDS, f"libraries.{key}", errors)
    for field, expected in (("display_name", str), ("date", str), ("description", str), ("production", bool)):
        if not isinstance(entry.get(field), expected):
            errors.append(f"libraries.{key}: \"{field}\" is required (a JSON {expected.__name__})")
    if isinstance(entry.get("date"), str):
        try:
            date_to_epoch(entry["date"])
        except ValueError:
            errors.append(f"libraries.{key}: \"date\" must be YYYY-MM-DD, it dates the files in the archive")
    published = published_categories(entry)
    if not published:
        errors.append(f"libraries.{key}: publishes no category (all of {', '.join(CATEGORIES)} are null or missing)")
    for category in published:
        _validate_category(entry[category], f"libraries.{key}.{category}", category, errors)


def validate_manifest(manifest):
    """Check the manifest's structure up front, so a typo or missing field
    gives one complete list of problems before any download starts."""
    errors = []
    if not isinstance(manifest, dict):
        raise RuntimeError("invalid manifest: top level must be a JSON object")
    _check_fields(manifest, MANIFEST_FIELDS, "manifest", errors)
    for field in ("cache_dir", "output_dir"):
        if not isinstance(manifest.get(field), str):
            errors.append(f"\"{field}\" is required (a string)")
    libraries = manifest.get("libraries")
    if not isinstance(libraries, dict) or not libraries:
        errors.append("\"libraries\" must be a non-empty object")
        libraries = {}
    for key, entry in libraries.items():
        _validate_library(key, entry, errors)

    if errors:
        raise RuntimeError(
            f"invalid manifest ({len(errors)} problem(s)):\n" + "\n".join(f"  - {error}" for error in errors)
        )


def load_json_file(path):
    with open(path, encoding="utf8") as handle:
        try:
            return json.load(handle)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid JSON in {path}: {exc}") from exc


def load_manifest(manifest_path):
    manifest = load_json_file(manifest_path)
    validate_manifest(manifest)
    return manifest


def selected_entries(manifest, only=None):
    """Yield (entry_key, spec) for every published category selected by only
    (a set of "KEY" / "KEY:category" values, or None for all)."""
    for key, entry in manifest["libraries"].items():
        for category in published_categories(entry):
            if only is not None and key not in only and f"{key}:{category}" not in only:
                continue
            # Entry-level date and production override the category's, so a
            # release's sublibraries cannot drift apart. neutron also takes the
            # entry's description and a synthesized display_name.
            spec = {**entry[category], "date": entry["date"], "production": entry["production"]}
            if category == "neutron":
                spec["display_name"] = f"{entry['display_name']} Neutron"
                spec["description"] = entry["description"]
            yield entry_key(key, category), spec


def format_manifest_listing(manifest):
    """One line per library key: its display name and published categories (the
    suffixes valid after "KEY:" for --only)."""
    lines = []
    for key, entry in manifest["libraries"].items():
        lines.append(f"{key:<10} {entry['display_name']:<15} {', '.join(published_categories(entry))}")
    return "\n".join(lines)


def validate_only(only, manifest):
    """Reject --only values naming no manifest key or published category."""
    valid = set()
    for key, entry in manifest["libraries"].items():
        valid.add(key)
        valid.update(f"{key}:{category}" for category in published_categories(entry))
    unknown = sorted(only - valid)
    if unknown:
        raise RuntimeError(
            f"unknown --only selection(s): {', '.join(unknown)}: run with --list to see the "
            "valid keys and categories"
        )
