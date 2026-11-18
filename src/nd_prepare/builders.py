# SPDX-FileCopyrightText: 2026 ENEA
# SPDX-FileContributor: Alberto P
#
# SPDX-License-Identifier: MPL-2.0

"""Build a library's payload directory (data files plus xsdir) from its sources.

Each mode builder extracts the archives into payload_dir and writes
payload_dir/xsdir. build_payload() then runs the mode-independent steps:
patches, tidying, the final xsdir pass (routes, blank records), nesting under
the library key and re-wrapping to 80 columns.
"""

import shutil
from pathlib import Path

from .common import log, log_wrapped
from .extract import extract_archive
from .sources import resolve_archive_source, source_label
from .xsdir import (
    NAME_FIELD,
    apply_xsdir_overrides,
    ensure_newline,
    extract_awr_table,
    filter_existing_entries,
    find_header_line,
    is_blank_record,
    is_xsdir_entry,
    iter_xsdir_records,
    normalize_xsdir_route,
    prefix_xsdir_filename,
    read_xsdir_text,
    rename_xsdir_entries,
    rename_xsdir_filenames,
    set_xsdir_filename,
    split_vendor_xsdir_header,
    strip_xsdir_filename_prefixes,
    wrap_xsdir_entries,
    write_xsdir_text,
    xsdir_record_fields,
)

# Where a builder parks the vendor's atomic-weight-ratio table until
# nest_payload_data moves it to docs/awr_<key>.
AWR_STAGING_NAME = "awr_table"


def stage_awr_table(header, payload_dir):
    """Keep the vendor's atomic-weight-ratio table as reference material: it
    cannot stay in the xsdir, but its AWRs may differ from nd_manager's."""
    awr_table = extract_awr_table(header)
    if awr_table is not None:
        write_xsdir_text(payload_dir / AWR_STAGING_NAME, awr_table)


def _extracted(root, name, what, check=Path.is_file):
    path = root / name
    if not check(path):
        raise RuntimeError(f"expected {what} not found after extraction: {name}")
    return path


def flatten_payload_dirs(payload_dir, dirnames):
    """Move everything under payload_dir/<dirname> up to payload_dir, for each
    dirname, and remove the emptied directory chain (both "ace" and "neutron"
    of "neutron/ace"). The caller strips the same prefixes from the xsdir."""
    for dirname in dirnames:
        src_dir = _extracted(payload_dir, dirname, "directory to flatten", Path.is_dir)
        for item in sorted(src_dir.iterdir()):
            target = payload_dir / item.name
            if target.exists():
                raise RuntimeError(f"cannot flatten {dirname}: {item.name} already exists in the payload root")
            item.rename(target)
        directory = src_dir
        while directory != payload_dir:
            try:
                directory.rmdir()
            except OSError as exc:
                leftover = ", ".join(sorted(p.name for p in directory.iterdir()))
                raise RuntimeError(
                    f"cannot flatten {dirname}: {directory.relative_to(payload_dir)} still holds {leftover}"
                ) from exc
            directory = directory.parent


def _extract_archives(spec, cache_dir, payload_dir, strip_components=0):
    seen = set()
    for archive in spec["archives"]:
        cached = resolve_archive_source(archive, cache_dir)
        written = extract_archive(cached, archive["kind"], payload_dir, strip_components)
        # Archives share one directory; they should be disjoint.
        collisions = sorted(seen.intersection(written))
        if collisions:
            log(f"{cached.name}: overwrote {len(collisions)} file(s) extracted from an earlier archive:")
            log_wrapped(collisions)
        seen.update(written)

    # Names that differ only in case are two files here but one on Windows and macOS.
    by_lowercase = {}
    for name in seen:
        by_lowercase.setdefault(name.lower(), []).append(name)
    clashes = sorted(" / ".join(sorted(names)) for names in by_lowercase.values() if len(names) > 1)
    if clashes:
        log(f"warning: {len(clashes)} file name(s) differ only in case; a case-insensitive file system keeps one:")
        log_wrapped(clashes)


def _read_vendor_xsdir(source, cache_dir, field):
    """(header, entries) of an xsdir source; raises if it has no "directory" line."""
    raw_text = read_xsdir_text(resolve_archive_source(source, cache_dir))
    header, entries = split_vendor_xsdir_header(raw_text)
    if entries == raw_text:
        raise RuntimeError(f"no 'directory' header line found in {field}: {source_label(source)}")
    return header, entries


def build_payload_ready(spec, cache_dir, payload_dir):
    _extract_archives(spec, cache_dir, payload_dir)

    # Flatten before reading the xsdir and strip its prefixes before filtering,
    # so the filter looks where the files now are.
    flatten_dirs = spec.get("flatten_dirs", [])
    flatten_payload_dirs(payload_dir, flatten_dirs)

    for old_name, new_name in spec.get("rename_members", {}).items():
        old_path = _extracted(payload_dir, old_name, "member to rename", Path.exists)
        new_path = payload_dir / new_name
        if new_path.exists():
            raise RuntimeError(f"cannot rename {old_name}: {new_name} already exists in the payload")
        old_path.rename(new_path)

    # Unlike drop_members, discarded files are removed before the xsdir is
    # filtered, so their xsdir entries go too.
    for discarded in spec.get("discard_members", []):
        discarded_path = _extracted(payload_dir, discarded, "member to discard")
        log(f"discarding {discarded}")
        discarded_path.unlink()

    xsdir_member = _extracted(payload_dir, spec["xsdir_member"], "xsdir member", Path.exists)
    xsdir_path = payload_dir / "xsdir"
    xsdir_member.rename(xsdir_path)
    raw_text = read_xsdir_text(xsdir_path)
    header, text = split_vendor_xsdir_header(raw_text)
    # The "directory" line is optional here: some vendors ship entries only.
    stray = find_header_line(raw_text) if text == raw_text else None
    if stray is not None:
        raise RuntimeError(f"{spec['xsdir_member']} has a header line ({stray!r}) but no 'directory' line")
    stage_awr_table(header, payload_dir)
    text = strip_xsdir_filename_prefixes(text, flatten_dirs)
    text = filter_existing_entries(text, payload_dir)
    if "rename_xsdir_tables" in spec:
        text = rename_xsdir_entries(text, spec["rename_xsdir_tables"])
    write_xsdir_text(xsdir_path, text)


def build_payload_fragments(spec, cache_dir, payload_dir):
    _extract_archives(spec, cache_dir, payload_dir)

    fragments = sorted(payload_dir.glob(spec["fragment_glob"]))
    if not fragments:
        raise RuntimeError(f"no xsdir fragments matched glob: {spec['fragment_glob']}")

    lines = []
    for fragment in fragments:
        rel_dir = fragment.parent.relative_to(payload_dir).as_posix()
        for record in iter_xsdir_records(read_xsdir_text(fragment)):
            if is_blank_record(record):
                continue
            if not is_xsdir_entry(record):
                raise RuntimeError(f"unexpected non-entry line in xsdir fragment {fragment}: {''.join(record).strip()}")
            # The fragment holds a bare filename; make it relative to the archive
            # root (a fragment at the root already is).
            if rel_dir != ".":
                record = prefix_xsdir_filename(record, rel_dir)
            lines.extend(ensure_newline(record))

    # Merged into xsdir, so the fragments are dead weight in the archive.
    for fragment in fragments:
        fragment.unlink()

    flatten_dirs = spec.get("flatten_dirs", [])
    flatten_payload_dirs(payload_dir, flatten_dirs)
    # No filter_existing_entries: every entry comes from a fragment of a payload file.
    text = strip_xsdir_filename_prefixes("".join(lines), flatten_dirs)
    if "rename_xsdir_files" in spec:
        text = rename_xsdir_filenames(text, spec["rename_xsdir_files"], payload_dir)
    write_xsdir_text(payload_dir / "xsdir", text)


def build_payload_merge_master(spec, cache_dir, payload_dir):
    _extract_archives(spec, cache_dir, payload_dir, 1 if spec.get("strip_top_level_dir") else 0)

    header, text = _read_vendor_xsdir(spec["xsdir_source"], cache_dir, "xsdir_source")
    stage_awr_table(header, payload_dir)
    if "xsdir_overrides" in spec:
        override_source = spec["xsdir_overrides"]
        _, override_text = _read_vendor_xsdir(override_source, cache_dir, "xsdir_overrides")
        text, replaced = apply_xsdir_overrides(text, override_text, override_source["entries"])
        log(f"xsdir: replaced {len(replaced)} entries with the versions from {source_label(override_source)}:")
        log_wrapped(replaced)
    # The xsdir may also list sibling sublibraries built from another archive.
    write_xsdir_text(payload_dir / "xsdir", filter_existing_entries(text, payload_dir))


BUILDERS = {
    "ready": build_payload_ready,
    "fragments": build_payload_fragments,
    "merge-master": build_payload_merge_master,
}


def _patch_xsdir_entry(extract_dir, file_spec):
    """The xsdir record for a patch file, from its single-entry fragment, with
    the fragment's placeholder filename replaced. Returns (zaid, record)."""
    fragment_name = file_spec["xsdir_fragment"]
    records = [
        record for record in iter_xsdir_records(read_xsdir_text(extract_dir / fragment_name))
        if not is_blank_record(record)
    ]
    if len(records) != 1 or not is_xsdir_entry(records[0]):
        raise RuntimeError(f"expected exactly one xsdir entry in patch fragment {fragment_name}")
    (record,) = records
    return xsdir_record_fields(record)[NAME_FIELD], ensure_newline(set_xsdir_filename(record, file_spec["dest"]))


def apply_patches(spec, cache_dir, payload_dir):
    """Overlay supplementary files released after a library's main archive.

    Each patch file replaces or adds a data file; with an xsdir_fragment, its
    entry replaces the one with the same ZAID, or is appended if none matches.
    """
    patches = spec.get("patches", [])
    if not patches:
        return
    xsdir_path = payload_dir / "xsdir"
    records = list(iter_xsdir_records(read_xsdir_text(xsdir_path)))
    for index, patch in enumerate(patches):
        cached = resolve_archive_source(patch, cache_dir)
        extract_dir = payload_dir.parent / f"patch_{index}"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_archive(cached, patch["kind"], extract_dir)

        for file_spec in patch["files"]:
            src = _extracted(extract_dir, file_spec["member"], "patch member")
            dest = payload_dir / file_spec["dest"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)

            if "xsdir_fragment" in file_spec:
                zaid, new_record = _patch_xsdir_entry(extract_dir, file_spec)
                for i, record in enumerate(records):
                    if is_xsdir_entry(record) and xsdir_record_fields(record)[NAME_FIELD] == zaid:
                        records[i] = new_record
                        break
                else:
                    if records:
                        records[-1] = ensure_newline(records[-1])
                    records.append(new_record)

        shutil.rmtree(extract_dir, ignore_errors=True)
    write_xsdir_text(xsdir_path, "".join(line for record in records for line in record))


def tidy_payload(spec, payload_dir):
    """Delete the drop_members, and move readme_member to a top-level README.md
    (nd_manager install renames it to docs/README_<LibraryName>.md)."""
    for dropped in spec.get("drop_members", []):
        _extracted(payload_dir, dropped, "member to drop").unlink()

    if "readme_member" in spec:
        readme = _extracted(payload_dir, spec["readme_member"], "readme member")
        if (payload_dir / "README.md").exists():
            raise RuntimeError(f"cannot move {spec['readme_member']} to README.md: the payload already has one")
        readme.rename(payload_dir / "README.md")


def finalize_xsdir(payload_dir):
    """Reset every entry's access route to 0 and drop blank records, whichever
    builder or patch produced them."""
    xsdir_path = payload_dir / "xsdir"
    text = normalize_xsdir_route(read_xsdir_text(xsdir_path))
    kept = [
        line
        for record in iter_xsdir_records(text) if not is_blank_record(record)
        for line in record
    ]
    write_xsdir_text(xsdir_path, "".join(kept))


def nest_payload_data(payload_dir, key):
    """Nest everything except xsdir and README.md under a <key>/ subdirectory,
    so nd_manager install gives each library its own data directory. The
    staged AWR table goes to docs/awr_<key> instead."""
    kept_at_root = {key, "xsdir", "README.md"}
    staged_awr = payload_dir / AWR_STAGING_NAME
    if staged_awr.exists():
        docs_dir = payload_dir / "docs"
        # Fail rather than mix the AWR table into, or hide, a vendor docs/.
        if docs_dir.exists():
            raise RuntimeError(f"cannot write docs/awr_{key}: the payload root already has a docs entry")
        docs_dir.mkdir()
        staged_awr.rename(docs_dir / f"awr_{key}")
        kept_at_root.add("docs")

    nested = payload_dir / key
    if nested.exists():
        raise RuntimeError(f"cannot nest the payload under {key}/: the payload root already has a {key} entry")
    nested.mkdir()
    for item in payload_dir.iterdir():
        if item.name not in kept_at_root:
            item.rename(nested / item.name)

    xsdir_path = payload_dir / "xsdir"
    rewritten = []
    for record in iter_xsdir_records(read_xsdir_text(xsdir_path)):
        rewritten.extend(prefix_xsdir_filename(record, key) if is_xsdir_entry(record) else record)
    write_xsdir_text(xsdir_path, "".join(rewritten))


def wrap_payload_xsdir(payload_dir):
    """Re-wrap the xsdir entries to the MCNP line width. Runs after
    nest_payload_data, whose <key>/ filename prefix lengthens the lines."""
    xsdir_path = payload_dir / "xsdir"
    write_xsdir_text(xsdir_path, wrap_xsdir_entries(read_xsdir_text(xsdir_path)))


def build_payload(spec, cache_dir, payload_dir, key):
    BUILDERS[spec["mode"]](spec, cache_dir, payload_dir)
    apply_patches(spec, cache_dir, payload_dir)
    tidy_payload(spec, payload_dir)
    finalize_xsdir(payload_dir)
    nest_payload_data(payload_dir, key)
    wrap_payload_xsdir(payload_dir)
