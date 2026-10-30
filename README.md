<!--
SPDX-FileCopyrightText: 2026 ENEA
SPDX-FileContributor: Alberto P

SPDX-License-Identifier: MPL-2.0
-->

# nd_prepare

Repackages seven external nuclear data libraries as a local repository for
[`nd_manager`](https://nucleardata.lanl.gov/), the nuclear data package manager
from Los Alamos National Laboratory (LANL). It is a separate companion tool,
written against the Python standard library only: run it yourself, then point an
installed `nd_manager` at the result.

## Libraries

Each library yields up to four entries, named `<NAME>_<CATEGORY>`:

| Library | Key | NEUTRON | TSL | PROTON | DOSIMETRY |
|---|---|---|---|---|---|
| FENDL-3.2c | `FENDL32C` | ✓ | —¹ | ✓ | — |
| FENDL-3.1d | `FENDL31D` | ✓ | —¹ | ✓ | — |
| FENDL-2.1 | `FENDL21` | ✓ | —¹ | — | — |
| D1SDATA | `D1SDATA` | ✓² | — | — | — |
| IRDFF-II | `IRDFF2` | — | — | — | ✓³ |
| JEFF-3.3 | `JEFF33` | ✓ | ✓ | ✓ | — |
| JEFF-4.0 | `JEFF40` | ✓ | ✓⁴ | ✓ | — |

1. Neither FENDL-3 release publishes an S(α,β) sublibrary (checked both IAEA
   pages and the `IAEA-NDS/FENDL-Processed` repo), and FENDL-2.1 lists only
   neutron ACE data, so FENDL is transport-only.
2. FENDL-3.1c-derived activation library for D1S shutdown dose rate. It is not
   published online: the zip must be supplied locally (see
   [Source entries](#source-entries)).
3. Response functions for foil activation, not transport data.
4. A **selected subset** of the TSL data: Be metal, D in D₂O, C in graphite, H
   in CH₂/H₂O/ZrH/ZrH1.5/ZrH2, O and U in UO₂, Zr in ZrH1.5/ZrH2. The full set
   is available from OECD-NEA on request.

To add a library, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Installation

Requires Python 3.9 or later, `curl` on the `PATH` (used for every download),
and an `nd_manager` installed separately.

```bash
pip install .        # or: pip install -e .  to edit the code or sources.json in place
```

This installs the `nd-prepare` command (also runnable as
`python3 -m nd_prepare`), with `sources.json` bundled as package data.
`--manifest` defaults to that bundled copy, not one in the current directory;
without `-e`, edits to the checkout's `sources.json` need a reinstall. `cache/`,
`output/` and `work/` are always relative to the current directory.

## Usage

```bash
nd-prepare                      # every library in sources.json
nd-prepare --list               # keys and categories accepted by --only
nd-prepare --only FENDL31D
nd-prepare --only JEFF40:neutron JEFF40:tsl
nd-prepare --only IRDFF2:dosimetry
```

Archives and `libraries.json` go to `output/`. Downloads are cached in `cache/`,
so re-runs fetch only what is missing. An unknown key or category is an error.

D1SDATA is built from local files, so a run that selects it (including a plain
`nd-prepare` or `--download-only`) stops with "local archive not found" unless
its zips are in the current directory. Without them, select the other
libraries: `nd-prepare --only FENDL32C FENDL31D FENDL21 IRDFF2 JEFF33 JEFF40`.

| Option | Effect |
|---|---|
| `--download-only` | Fill `cache/` and exit; a later run builds offline from cache. |
| `--cleanup` | Once each entry is packed, delete its extracted payload **and** its cached downloads. |
| `--cleanup-work` | Once each entry is packed, delete only its extracted payload; keep `cache/`. |
| `--compression xz` | `.tar.xz` (smaller, slower) instead of `.tar.gz`. |
| `--prune` | Remove `libraries.json` entries the manifest no longer produces, delete their archives, and exit. |
| `--workdir DIR` | Extraction scratch directory (default `./work`). |
| `--manifest FILE` | Use another manifest. |

Compression levels are low (gzip 6, xz preset 1): ACE data is dense ASCII and
gains little from higher levels. Rebuilding an entry with the other compression
points `libraries.json` at the new archive but leaves the old one in `output/`;
`--prune` does not remove it, so delete it by hand.

Register the result with `nd_manager`:

```bash
nd_manager config --add-repo ExternalND /path/to/output/libraries.json
nd_manager update
nd_manager list
```

### Output layout

Inside each archive, everything except `xsdir`, `README.md` and `docs/` sits
under `<NAME>_<CATEGORY>/`, like LANL's own libraries (`kidman.tgz`,
`endf66.tgz`). Each installed library therefore gets its own data directory, and
filenames from different libraries cannot collide.

`libraries.json` accumulates across runs, so separate `--only` runs build one
repository. As a consequence, entries for removed or renamed libraries stay
listed (and offered by `nd_manager`) until you run `--prune`. Every build run
warns about them (`--download-only` does not). Entries from another `--manifest`
or added by hand also count as stale, so check the warning before pruning.

## Size and licensing

A full run (with the D1SDATA zips in place) downloads roughly **24-26 GB**,
mostly JEFF; D1SDATA's 425 MB is local and not part of that. The main items:

- JEFF-4.0 and JEFF-3.3 transport archives: about 1.3 GB each, 8 and 6 of them
- JEFF-4.0 TSL archive: about 4.4 GB
- four proton archives: under 1 GB combined
- D1SDATA: about 425 MB
- IRDFF-II: about 21 MB

Plan for at least 60 GB free for a full run (downloads, extracted payloads and
archives); `--cleanup-work` lowers the peak. Use `--only` to download less.

| Library | License |
|---|---|
| JEFF-4.0, JEFF-3.3 | CC-BY-4.0 (checked in the metadata on `data.oecd-nea.org`). |
| FENDL, IRDFF-II | Only IAEA's general website terms; no explicit open-data license was found. Review them before sharing the archives. |
| D1SDATA | **Confidential.** No published license; do not share the data or archives built from it outside your organization. |

### Data distribution

Every `url` source in `sources.json` is openly published (IAEA, OECD-NEA open
records). Data obtained on request or under specific terms, such as the full
JEFF-4.0 TSL set or D1SDATA, enters only through `path` sources and is never
committed. D1SDATA is confidential: do not commit, publish or redistribute it,
the archives built from it, or a `libraries.json` that serves them outside your
organization.

## Caveats when combining libraries

### Duplicate table names

Each run warns when a table name is defined more than once, within or across the
processed libraries. Installing such libraries together gives the merged xsdir
duplicate entries, and MCNP® uses the first match.

### ZAID conventions

ZAIDs for metastable states differ between libraries, and for Am-242 the
conventions conflict:

| Library | Metastable rule | Am-242 (ground) | Am-242m |
|---|---|---|---|
| LANL libraries | — | `95642` | `95242` |
| JEFF-3.3 | MCNP: `A + 300 + 100*m` | `95242` | `95642` |
| JEFF-4.0 | `300 + 100*(m-1) + (A mod 100)` | `95242` | `95342` |

FENDL, D1SDATA and IRDFF-II have neither nuclide. `nd_prepare` neither renumbers
nor checks these ZAIDs. MCNP reads the ZAID from the ACE header, so an
xsdir-only rename would make MCNP fail, and rewriting the header would alter
vendor data. When combining libraries:

- do not mix JEFF-3.3 and JEFF-4.0 metastable nuclides;
- do not trust the Am-242 ZAID alone: check the data file the xsdir entry points
  to (`95-Am-242g-*` or `95-Am-242m-*`);
- keep only one source for Am-242 in a given xsdir.

## How it works

`nd_manager install` needs one top-level directory plus an `xsdir`. None of the
providers ship that, so each library is rebuilt.

### Transforms

- **Flatten** vendor wrapper directories (`fendl31d_ACE/`, `ace/`,
  `neutron/ace/`, `d1sdata/`, ...) and rewrite the xsdir filenames to match
  (`ready`, `fragments`; `merge-master` strips each archive's top-level
  directory instead). Merged fragments (`.xsd`, `.xdr`, `.dir`) are deleted.
- **Strip the vendor header** of master xsdirs (`ready`, `merge-master`):
  `datapath=...`, sometimes an atomic-weight-ratio table, then a `directory`
  line. A repository `xsdir` holds only entries. An atomic-weight-ratio table is
  saved as reference in `docs/awr_<NAME>_<CATEGORY>`, but nothing reads it.
  `merge-master` requires the `directory` line. `ready` also accepts an xsdir of
  entries only, but one with a header and no `directory` line is an error.
- **Drop entries whose file is missing** from the payload (`ready`,
  `merge-master`). Dropped entries are logged by ZAID and do not fail the build
  unless none is left; some are expected (JEFF-4.0's xsdir lists far more TSL
  tables than are published as ACE).
- **Final pass** on every `xsdir`, in all modes: reset every access route to
  `0`, drop blank and control-character-only records, and end the file with a
  newline.
- **Line width** (last step, after the `<NAME>_<CATEGORY>/` filename prefix is
  added, which lengthens the lines): every entry is rebuilt as one logical line
  (its continuations joined) and word-wrapped again to at most 80 columns, the
  MCNP limit. An entry that fits is written on one line. A longer one is broken
  with a trailing ` +` on each line but the last, and its continuation lines are
  indented by 10 spaces. A field that cannot fit in 80 columns fails the build.

xsdir entries are handled as whole records. A line ending in `+` continues onto
the next (MCNP continuation format, used by FENDL-3.1d neutron), and dropping or
rewriting an entry covers its continuation lines. A record has at least the 7
mandatory fields: ZAID, AWR, filename, access route, file type, address and
table length.

### Per-library fixes

Most of these were found by comparing each xsdir entry with its ACE header
(table name, AWR, kT, NXS(1) table length).

| Library / category | Upstream issue | Fix (manifest field) |
|---|---|---|
| FENDL-3.2c neutron, proton | One data file and one `.xsd` fragment per isotope; no combined xsdir. | Fragments are concatenated (`fragments` mode). |
| FENDL-3.1d neutron | Two loose xsdir templates (`_Linux.xsd`, `_Windows.xsd`). | `_Linux.xsd` is used; `_Windows.xsd` is deleted (`drop_members`). |
| FENDL-3.1d neutron | Two K-39 files share the table name `19039.31c`: `19K_039` (TENDL-2015) and `19K_039_` (TENDL-2010). | `19K_039_` and its entry are discarded (`discard_members`). |
| FENDL-3.1d neutron | The xsdir ends with a DOS end-of-file byte (`0x1A`). | The byte is dropped in the final pass. |
| FENDL-3.1d proton | Per-isotope fragments (`.ace` + `.xdr`); no combined xsdir. | Fragments are concatenated (`fragments` mode). |
| FENDL-2.1 neutron | One zip per isotope or natural element (71), each with an `.ace` and a one-entry `.dir` fragment; no combined xsdir. | Fragments are concatenated from all archives (`fragments` mode). |
| FENDL-2.1 neutron | Seven `.dir` fragments (He-3, He-4, Li-6, Li-7, Be-9, Cl-35, Cl-37) name an upper-case file (`HE003.ace`), but the zip holds `He003.ace`; on a case-sensitive file system the entries would be dropped. | The xsdir filenames are rewritten to the real spelling (`rename_xsdir_files`); the ACE files are untouched. |
| JEFF-4.0 neutron | One flat zip per temperature (8) plus a separate master xsdir. | `merge-master` mode. |
| JEFF-4.0 neutron | `jeff4.xsdir` (NEA record 0vhn5-d6t91) gives 14 table lengths for Cs-134 and Sm-150 (`.40c`-`.46c`) smaller than the ACE NXS(1). | Those entries are taken from record 53ret-j3386 (`xsdir_overrides`). |
| JEFF-3.3 neutron, TSL | Neutron is one tar.gz per temperature (6); each archive is wrapped in its own directory (`ace_293/`, ...), while the master xsdir uses bare names. | The wrapper directories are stripped (`strip_top_level_dir`). |
| JEFF-3.3 neutron, TSL | The master `xsdir.jeff33` lists both neutron and TSL entries. | Each sublibrary keeps only entries whose file is in its payload. |
| JEFF-4.0 TSL | Data nested in `data/`, but `jeff4.xsdir` uses bare names. | `data/` is flattened. |
| JEFF-4.0 TSL | Four table names cover 32 temperatures each (see below). | `.01` series discarded (`discard_members`); tables renamed (`rename_xsdir_tables`). |
| JEFF-3.3 proton | The entries file names the data file `tendlh`, but the archive holds `tendl17h`. Access route is `iaea`. | File renamed (`rename_members`); route reset in the final pass. Readme `Ace-Readme.tendl17h` becomes `README.md` (`readme_member`); `graphs.tgz` deleted (`drop_members`). |
| JEFF-4.0 proton | The xsdir has no trailing newline. | Added in the final pass. |
| D1SDATA | Data in `d1sdata/`, `xsdir_d1s` loose at the zip root, trailing blank lines. | `d1sdata/` flattened; blanks dropped in the final pass. |
| D1SDATA | The xsdir lists a `Cl35.ace` missing from the zip. | Entry dropped. |
| D1SDATA | Revised and new tables released after the main archive. | Two patches applied (see [Patches](#patches)). |
| IRDFF-II | The master `IRDFF-II.xsd` uses Windows backslash paths (unsupported); per-isotope `.xsd` fragments also ship. | Fragments used; master deleted (`drop_members`). |

**JEFF-4.0 TSL UO₂ tables.** `ouo2.00t`, `ouo2.01t`, `uuo2.00t` and `uuo2.01t`
each name 32 files, one per temperature (128 of 316 entries), so a temperature
cannot be selected by table name. The `.01` series is the same evaluation
processed 1 K away from the `.00` one (suspected processing bug), so its 64
files are discarded. The remaining 64 tables are renumbered in the xsdir only
(ACE headers untouched) to `ouo2.00t`-`ouo2.31t` and `uuo2.00t`-`uuo2.31t`,
ascending with temperature (285 K to 1700 K), so 62 of them get a new name. The
same index means the same temperature in both materials.

### `sources.json`

Each library entry has `neutron`, `tsl`, `proton` and `dosimetry` keys, `null`
for categories it does not publish. `date` and `production` are set once per
entry and merged into each category, so the sublibraries of one release cannot
drift apart. `mode` selects the build strategy:

| Mode | Used for | What it does |
|---|---|---|
| `ready` | FENDL-3.1d neutron, D1SDATA, JEFF-4.0 TSL, JEFF proton | Applies `flatten_dirs` and `rename_members`, renames `xsdir_member` to `xsdir`, then applies the common transforms. |
| `fragments` | FENDL-3.2c, FENDL-3.1d proton, FENDL-2.1, IRDFF-II | Extracts all archives into one directory, concatenates the fragments matched by `fragment_glob`, prefixing each filename with its fragment's directory (none for a fragment at the archive root), deletes the fragments, then applies `flatten_dirs` and `rename_xsdir_files`. A fragment may hold several entries and `+` continuation lines; any other non-blank line is an error. |
| `merge-master` | JEFF neutron, JEFF-3.3 TSL | Extracts all archives into one directory (`strip_top_level_dir` optionally strips each archive's top-level directory), then builds the `xsdir` from the separate `xsdir_source`. |

Optional fields:

| Field | Modes | Effect | Error if |
|---|---|---|---|
| `flatten_dirs` | `ready`, `fragments` | Moves the contents of these directories (possibly nested, e.g. `neutron/ace`) to the payload root. A leading `<dir>/` is stripped from the xsdir filenames that have it, so the vendor xsdir may use either prefixed names (FENDL-3.1d's `fendl31d_ACE/`) or bare ones (JEFF-4.0 TSL's `data/`). | directory missing, name collision, leftover content |
| `rename_members` | `ready` | Renames payload files. | file missing, target already exists |
| `discard_members` | `ready` | Deletes files before the xsdir is filtered, so their entries go too. | file missing |
| `rename_xsdir_files` | `fragments` | Maps an xsdir filename to the real spelling of the data file, after `flatten_dirs`. The data file is not modified. | new file not in the payload; old file not in the xsdir |
| `rename_xsdir_tables` | `ready` | Maps a data file name to the new table name of its xsdir entry, after missing-file entries are dropped. The ACE file is not modified. | file not in xsdir, name still repeated; two files mapped to one name fail validation |
| `drop_members` | any | Deletes files after the build and patches. | file missing |
| `readme_member` | any | Moves a vendor readme to `README.md` at the archive root. | file missing, `README.md` already exists |
| `strip_top_level_dir` | `merge-master` | `true` strips each archive's top-level directory on extraction (JEFF-3.3's `ace_293/`, ...). | |
| `xsdir_overrides` | `merge-master` | A second xsdir source (`url`/`path` + `md5`). Only the table names in its `entries` (e.g. `55134.40c`) are taken from it, replacing the same-named entries in place; changes are logged. | name missing from either xsdir |
| `cache_name` | any `url` source | File name under `cache/<NAME>_<CATEGORY>/`, for URLs that share a basename (both JEFF-4.0 xsdirs are `jeff4.xsdir`). | not a plain file name |

The manifest is validated before any download. An unknown field (e.g.
`drop_member`), or one the mode does not read (e.g. `discard_members` in
`merge-master`), is an error. So is a library key that is not letters and digits
only, and a member path that is absolute or contains `..`.

#### Source entries

A source in `archives`, `xsdir_source`, `xsdir_overrides` or `patches` gives
either a `url` (downloaded and cached under `cache/`) or a `path` relative to
the current directory, for unpublished data. For example, D1SDATA uses:

```json
{"path": "d1sdata_YTF5QD_v1_0.zip", "kind": "zip", "md5": "f2f268a2b95d06046dac72631223d554"}
```

Run `nd-prepare` from the directory holding that file. Keep that directory
outside the git checkout: local sources are not public, and `.gitignore`
excludes `*.zip` only as a safety net.

Every source requires an `md5`, checked on each download, cache hit and local
read. A mismatch aborts the run, and a bad cached download is deleted so the
next run downloads it again. An interrupted download resumes on the next run, or
starts over if the server cannot resume it. Where the checksums come from:

- **JEFF**: published by the vendor (`data.oecd-nea.org` record API);
- **FENDL, IRDFF-II**: cross-checked by downloading twice, with `curl` and
  `wget`;
- **D1SDATA**: computed from the single local file.

#### Patches

`patches` adds data released after a library's main archive. D1SDATA has two: a
revised `Nb93.ace`, and a revised `Cu65.ace` plus new `Re185.ace` and
`Sb121.ace`. Each patch is a source (`path` or `url`, plus `kind` and `md5`)
with a `files` list, applied after the base payload is built:

| Field | Meaning |
|---|---|
| `member` | The file's path inside the patch archive. |
| `dest` | Destination relative to the payload root after `flatten_dirs`, without `..`. An existing file is overwritten. |
| `xsdir_fragment` (optional) | A member holding one xsdir entry for the file. It replaces the entry with the same ZAID, or is appended if there is none. Omit it when the ZAID and table length are unchanged (D1SDATA's Nb93 patch). |

D1SDATA's fragments are fill-in-the-blank templates, with the literal words
`filename` and `route` in those fields. Both fields are overwritten outright
(the route is always `0`), not pattern-matched.

## Code layout

| File (`src/nd_prepare/`) | Role |
|---|---|
| `cli.py` | Arguments and orchestration |
| `common.py` | Shared constants and logging |
| `prepare.py` | Per-entry build, `libraries.json`, prune |
| `builders.py` | The three modes, patches, nesting |
| `xsdir.py` | xsdir text handling |
| `sources.py` | Download, md5 |
| `extract.py` | Safe zip/tar extraction |
| `pack.py` | Reproducible archives; every file is stamped with the library's `date` from `sources.json` |
| `manifest.py` | Manifest loading, validation and entry selection |
| `sources.json` | The manifest, bundled as package data |

## End-to-end verification

`tests/verify_full_run.py` is a pre-release check, not a unit test suite. It:

- runs the whole manifest through `nd-prepare` (the full ~24-26 GB download);
- installs every archive into a throwaway `nd_manager` profile (this needs a
  sibling `nd_manager` checkout, or `--nd-manager-path`);
- checks that every installed file is under its library's own directory, and
  that each xsdir, per library and merged, ends with a newline, has no blank
  lines, and gives every entry route `0` and a file that exists.

Libraries whose local `path` sources are missing from `--workdir` (D1SDATA) are
skipped. To run it:

- **Smoke test**: `--only IRDFF2:dosimetry`.
- **Full run on a cluster**: `tests/run_verify_full.slurm` submits it to an
  offline compute node.
- **Less disk**: `--cleanup-work` is forwarded to `nd-prepare`, which deletes
  each entry's unpacked payload as it goes (the cache is kept).

### Independent md5 and xsdir validation

Three more scripts check the installed result against the vendor data. They use
only the Python standard library, never `nd_prepare`, `nd_manager` or
`sources.json`, so a bug in the tool cannot hide in the check:

```bash
python3 tests/validate_md5.py           --workdir DIR [--jobs N] [--only LIB ...]
python3 tests/validate_xsdir.py         --workdir DIR [--strict] [--detail] [--only LIB ...]
python3 tests/validate_downloads_md5.py --downloads DIR --data DIR [--jobs N] [--only LIB ...]
```

- `validate_md5.py`: every installed file is byte-identical to a member of a
  vendor archive.
- `validate_downloads_md5.py`: every file `nd_manager` installed is
  byte-identical to its member in the archive `nd_manager` kept
  (`keep downloads`). It streams each archive in memory, one worker per archive.
- `validate_xsdir.py`: every xsdir entry matches the vendor xsdir field by
  field, except the file path and a placeholder route, and agrees with the
  header of its ACE file. `xsdir_all`, the merged xsdir that `nd_manager`
  writes to `MCNP_DATA/`, holds exactly the per-library entries.

What `validate_md5.py` and `validate_xsdir.py` expect is written by hand in
`tests/validation_expected.json` (its keys are described in `tests/README.md`):
where each original xsdir is, whether renames are allowed (JEFF-4.0 TSL), and
the counts from a reviewed full run. A changed count is a warning, or an error
with `--strict`; once reviewed, `validate_xsdir.py --write-expected-counts`
updates them. A library listed there but not installed is an error, unless
`--only` is given. That includes D1SDATA when its zips are missing.

`validate_md5.py` and `validate_xsdir.py` write their reports to
`DIR/validation/`, and `tests/run_verify_full.slurm` runs both after
`verify_full_run.py`. `validate_downloads_md5.py` only prints its result; it
needs `nd_manager`'s kept downloads and is run by hand.

## Trademarks and affiliation

MCNP® and Monte Carlo N-Particle® are registered trademarks owned by Triad
National Security, LLC, manager and operator of Los Alamos National Laboratory
for the U.S. Department of Energy. `nd_prepare` is an independent ENEA project
and is not affiliated with or endorsed by Triad National Security, LLC, Los
Alamos National Laboratory, the IAEA, or the OECD Nuclear Energy Agency. Data
library names (FENDL, JEFF, IRDFF, D1SDATA) are used only to identify the data
sets that `nd_prepare` downloads.

What `nd_prepare` knows of the xsdir format (the 80-column limit, `+`
continuation lines, the entry fields), of the `nd_manager` format and of the ACE
format comes from publicly available sources, listed in
[References](#references). This repository contains no MCNP® source code,
executables or data libraries.

## References

- Colin Josey, Jeremy Conlin, *LANL Nuclear Data Manager Format Specification
  v1.0* (2022),
  [doi:10.2172/1880470](https://doi.org/10.2172/1880470)
- Jeremy Conlin, Wim Haeck, Paul Romano, *A Compact ENDF (ACE) Format
  Specification* (2026),
  [doi:10.2172/3427064](https://doi.org/10.2172/3427064)
- Los Alamos National Laboratory, [*LA-UR-25-26264*](https://mcnp.lanl.gov/pdf_files/TechReport_2025_LANL_LA-UR-25-26264_Kleedtke.pdf) (2025): the fields
  of an xsdir entry
- Joel Kulesza, Terry Adams, Jerawan Armstrong, Simon Bolding, Forrest Brown,
  Jeffrey Bull, Timothy Burke, Alexander Clark, Robert Forster III, Jesse Giron,
  Tristan Grieve, Colin Josey, Roger Martz, Gregg McKinney, Eric Pearson,
  Michael Rising, Clell Solomon Jr., Swaminarayan Sriram, Travis Trahan,
  Colin Weaver, Stephen Wilson, Anthony Zukaitis, *MCNP® Code Version 6.3.1
  Theory & User Manual* (2024),
  [doi:10.2172/2372634](https://doi.org/10.2172/2372634)
