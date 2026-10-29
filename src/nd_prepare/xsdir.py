# SPDX-FileCopyrightText: 2026 ENEA
# SPDX-FileContributor: Alberto P
#
# SPDX-License-Identifier: MPL-2.0

"""Text-level helpers for MCNP xsdir files.

A "record" is the list of physical lines of one logical entry: an entry
continues onto the next line when a line's last token is "+".
"""

from .common import log, log_wrapped, plural_entries

# An entry has 7 required fields: ZAID, AWR, filename, access route, file
# type, address and table length; record length, entries per record,
# temperature and ptable flag are optional.
MIN_XSDIR_ENTRY_FIELDS = 7
# Positions of the fields the code reads or rewrites.
NAME_FIELD = 0
FILENAME_FIELD = 2
ROUTE_FIELD = 3
# latin-1 maps every byte to one character, so any vendor file reads and
# round-trips unchanged whatever its encoding.
XSDIR_ENCODING = "latin-1"
# MCNP reads at most 80 columns of an xsdir line (newline excluded).
XSDIR_MAX_LINE = 80
XSDIR_CONTINUATION_INDENT = "          "


def read_xsdir_text(path):
    # CRLF and lone CR become "\n", the only line ending the rewrites produce.
    return path.read_text(encoding=XSDIR_ENCODING).replace("\r\n", "\n").replace("\r", "\n")


def _split_lines(text):
    """The lines of text, ends kept, split at newlines only: str.splitlines
    also splits at form feeds, 0x1C-0x1E and 0x85, and a rewrite would then
    fuse two lines."""
    lines = [line + "\n" for line in text.split("\n")]
    lines[-1] = lines[-1][:-1]
    return lines if lines[-1] else lines[:-1]


def write_xsdir_text(path, text):
    # Newline-terminated, so a file ending mid-line cannot fuse with the next
    # file's first line on concatenation.
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding=XSDIR_ENCODING)


def split_vendor_xsdir_header(text):
    """Split a vendor xsdir into (header, entries), the literal "directory" line
    being in neither. nd_manager writes its own "directory" line and expects
    pure entries, so a vendor header must not stay. If there is no "directory"
    line, the header is empty and the entries are the text unchanged.
    """
    lines = _split_lines(text)
    for index, line in enumerate(lines):
        if line.strip().lower() == "directory":
            return "".join(lines[:index]), "".join(lines[index + 1:])
    return "", text


def find_header_line(text):
    """The first "datapath" or "atomic weight ratios" line of text, or None.
    These belong only before a "directory" line; without one, the lines
    after them would be read as entries."""
    for line in _split_lines(text):
        stripped = line.strip().lower()
        if stripped.startswith("datapath") or stripped == "atomic weight ratios":
            return line.strip()
    return None


def extract_awr_table(header):
    """The "atomic weight ratios" block of a vendor xsdir header (without any
    "datapath=" line), or None if it has none."""
    lines = [
        line for line in _split_lines(header)
        if not line.strip().lower().startswith("datapath")
    ]
    for index, line in enumerate(lines):
        if line.strip().lower() == "atomic weight ratios":
            return "".join(lines[index:]).rstrip() + "\n"
    return None


def iter_xsdir_records(text):
    """Yield each logical xsdir record as a list of its physical lines."""
    record = []
    for line in _split_lines(text):
        record.append(line)
        if line.split()[-1:] != ["+"]:
            yield record
            record = []
    if record:
        yield record


def is_blank_record(record):
    """True if nothing printable is in the record. Unlike str.strip(), this also
    catches control characters such as the DOS end-of-file marker 0x1A."""
    return not any(ch.isprintable() and not ch.isspace() for ch in "".join(record))


def xsdir_record_fields(record):
    """All fields of a record, minus the "+" continuation markers."""
    fields = []
    for line in record:
        line_fields = line.split()
        if line_fields[-1:] == ["+"]:
            line_fields = line_fields[:-1]
        fields.extend(line_fields)
    return fields


def is_xsdir_entry(record):
    return len(xsdir_record_fields(record)) >= MIN_XSDIR_ENTRY_FIELDS


def ensure_newline(record):
    if record[-1].endswith("\n"):
        return record
    return record[:-1] + [record[-1] + "\n"]


def _rewrite_xsdir_field(record, index, rewrite):
    """Return record with the field at index of its first line replaced by
    rewrite(field). The first line is re-joined with single spaces; its
    trailing "+" and line ending, and the continuation lines, are kept."""
    first = record[0]
    fields = first.split()
    continued = fields[-1:] == ["+"]
    if continued:
        fields = fields[:-1]
    if len(fields) <= index:
        raise RuntimeError(f"xsdir entry has no field {index} on its first line: {first.strip()}")
    fields[index] = rewrite(fields[index])
    if continued:
        fields.append("+")
    newline = "\n" if first.endswith("\n") else ""
    return [" ".join(fields) + newline] + record[1:]


def set_xsdir_filename(record, filename):
    return _rewrite_xsdir_field(record, FILENAME_FIELD, lambda _: filename)


def prefix_xsdir_filename(record, prefix):
    return _rewrite_xsdir_field(record, FILENAME_FIELD, lambda filename: f"{prefix}/{filename}")


def strip_xsdir_filename_prefixes(text, prefixes):
    """Remove a leading prefix/ from each entry's filename field, for every
    prefix in prefixes. Entries without it and non-entry records are left alone.
    """
    rewritten = []
    for record in iter_xsdir_records(text):
        if is_xsdir_entry(record):
            for prefix in prefixes:
                filename = xsdir_record_fields(record)[FILENAME_FIELD]
                if filename.startswith(f"{prefix}/"):
                    record = set_xsdir_filename(record, filename[len(prefix) + 1:])
        rewritten.extend(record)
    return "".join(rewritten)


def normalize_xsdir_route(text):
    """Set every entry's access route to "0", the local-file route.
    Some vendors put provenance there (JEFF-3.3 proton: "iaea")."""
    rewritten = []
    changed = 0
    for record in iter_xsdir_records(text):
        if is_xsdir_entry(record) and xsdir_record_fields(record)[ROUTE_FIELD] != "0":
            record = _rewrite_xsdir_field(record, ROUTE_FIELD, lambda _: "0")
            changed += 1
        rewritten.extend(record)
    if changed:
        log(f"xsdir: reset the access route of {changed} {plural_entries(changed)} to 0")
    return "".join(rewritten)


def filter_existing_entries(text, payload_dir):
    """Drop entries whose data file is not in payload_dir.

    Vendors ship xsdirs listing more files than exist, and one xsdir may cover
    several sublibraries. Whole records are kept or dropped, continuation lines
    included; non-entry records are kept. Dropped ZAIDs are logged, not errors.
    """
    kept = []
    dropped = []
    for record in iter_xsdir_records(text):
        fields = xsdir_record_fields(record)
        if is_xsdir_entry(record) and not (payload_dir / fields[FILENAME_FIELD]).is_file():
            dropped.append(fields[NAME_FIELD])
        else:
            kept.extend(record)
    if dropped:
        log(f"xsdir: dropped {len(dropped)} {plural_entries(len(dropped))} whose data file is not in this payload:")
        log_wrapped(dropped)
    return "".join(kept)


def _rename_listed_files(text, renames, option, rename_record):
    """Rewrite every entry whose data file is a key of renames:
    rename_record(record, fields, new_value) returns the new record. A file
    that is not in the xsdir is an error, reported under the manifest option."""
    found = set()
    rewritten = []
    for record in iter_xsdir_records(text):
        if is_xsdir_entry(record):
            fields = xsdir_record_fields(record)
            if fields[FILENAME_FIELD] in renames:
                found.add(fields[FILENAME_FIELD])
                record = rename_record(record, fields, renames[fields[FILENAME_FIELD]])
        rewritten.extend(record)
    missing = renames.keys() - found
    if missing:
        raise RuntimeError(f"{option}: file(s) not in the xsdir: {', '.join(sorted(missing))}")
    return "".join(rewritten)


def rename_xsdir_entries(text, renames):
    """Give each data file in renames the new table name of its entry. A table
    name still repeated afterwards is an error."""
    def rename_record(record, fields, name):
        log(f"xsdir: renamed {fields[NAME_FIELD]} ({fields[FILENAME_FIELD]}) to {name}")
        return _rewrite_xsdir_field(record, NAME_FIELD, lambda _: name)

    renamed = _rename_listed_files(text, renames, "rename_xsdir_tables", rename_record)
    names = xsdir_text_table_names(renamed)
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise RuntimeError(f"rename_xsdir_tables: table name(s) still repeated: {', '.join(duplicates)}")
    return renamed


def rename_xsdir_filenames(text, renames, payload_dir):
    """Replace each xsdir filename in renames by the real spelling of the data
    file, which must be in payload_dir."""
    def rename_record(record, fields, new_name):
        if not (payload_dir / new_name).is_file():
            raise RuntimeError(f"rename_xsdir_files: {new_name} is not in the payload")
        log(f"xsdir: renamed file {fields[FILENAME_FIELD]} of {fields[NAME_FIELD]} to {new_name}")
        return set_xsdir_filename(record, new_name)

    return _rename_listed_files(text, renames, "rename_xsdir_files", rename_record)


def apply_xsdir_overrides(text, override_text, names):
    """Replace the entries of text named in names (table names, e.g. "55134.40c") by
    the same-named entries of override_text. Every name must exist in both, or
    this raises. Returns (new text, names whose entry actually changed)."""
    wanted = set(names)
    overrides = {}
    for record in iter_xsdir_records(override_text):
        if is_xsdir_entry(record) and xsdir_record_fields(record)[NAME_FIELD] in wanted:
            overrides[xsdir_record_fields(record)[NAME_FIELD]] = record
    missing = wanted - overrides.keys()
    if missing:
        raise RuntimeError(f"xsdir_overrides: not in the override xsdir: {', '.join(sorted(missing))}")
    out = []
    replaced = []
    seen = set()
    for record in iter_xsdir_records(text):
        if is_xsdir_entry(record):
            name = xsdir_record_fields(record)[NAME_FIELD]
            if name in wanted:
                seen.add(name)
                new = overrides[name]
                if xsdir_record_fields(new) != xsdir_record_fields(record):
                    replaced.append(name)
                    record = ensure_newline(new)
        out.extend(record)
    if wanted - seen:
        raise RuntimeError(f"xsdir_overrides: not in the master xsdir: {', '.join(sorted(wanted - seen))}")
    return "".join(out), replaced


def wrap_xsdir_record(record, width=XSDIR_MAX_LINE):
    """Return record re-wrapped from scratch: the continuation lines are joined
    into one logical line, which is kept as is if it fits in width and
    otherwise broken greedily, each line but the last ending in " +" and no
    line longer than width. A field too long for any line is an error."""
    fields = xsdir_record_fields(record)
    newline = "\n" if record[-1].endswith("\n") else ""
    single = " ".join(fields)
    if len(single) <= width:
        return [single + newline]

    lines = []
    current = ""
    for field in fields:
        candidate = f"{current} {field}" if current else field
        # Always reserve room for " +", as any line may turn out not to be the last.
        if len(candidate) + 2 <= width:
            current = candidate
            continue
        if not current:
            raise RuntimeError(f"xsdir field too long to wrap in {width} columns: {field}")
        lines.append(current + " +")
        current = f"{XSDIR_CONTINUATION_INDENT}{field}"
        if len(current) + 2 > width:
            raise RuntimeError(f"xsdir field too long to wrap in {width} columns: {field}")
    lines.append(current)
    return [line + "\n" for line in lines[:-1]] + [lines[-1] + newline]


def wrap_xsdir_entries(text, width=XSDIR_MAX_LINE):
    """Re-wrap every entry of an xsdir to at most width columns, which also
    collapses its spacing to single blanks. Non-entry records are left alone."""
    rewritten = []
    changed = 0
    for record in iter_xsdir_records(text):
        if is_xsdir_entry(record):
            wrapped = wrap_xsdir_record(record, width)
            if wrapped != record:
                changed += 1
            record = wrapped
        rewritten.extend(record)
    if changed:
        log(f"xsdir: re-wrapped {changed} {plural_entries(changed)} to at most {width} columns")
    return "".join(rewritten)


def xsdir_text_table_names(text):
    return [xsdir_record_fields(record)[NAME_FIELD] for record in iter_xsdir_records(text) if is_xsdir_entry(record)]


def xsdir_table_names(xsdir_path):
    return xsdir_text_table_names(read_xsdir_text(xsdir_path))


def check_xsdir_ascii(xsdir_path):
    """Fail if the xsdir holds a non-ASCII byte: nd_manager reads it as UTF-8,
    so it would otherwise only fail at install time."""
    for lineno, line in enumerate(read_xsdir_text(xsdir_path).split("\n"), start=1):
        for col, char in enumerate(line, start=1):
            if ord(char) > 0x7F:
                raise RuntimeError(
                    f"non-ASCII byte 0x{ord(char):02X} in {xsdir_path} "
                    f"line {lineno}, column {col}: {line.strip()!r}"
                )
