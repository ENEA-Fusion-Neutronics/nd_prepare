<!--
SPDX-FileCopyrightText: 2026 ENEA
SPDX-FileContributor: Alberto P

SPDX-License-Identifier: MPL-2.0
-->

# Pre-release checks

These scripts check a full run end to end. They are not a unit test suite. They
use only the Python standard library and never import `nd_prepare` or
`nd_manager`. Only `verify_full_run.py` reads `src/nd_prepare/sources.json`, to
find missing local sources.

| Script | What it does |
|---|---|
| `verify_full_run.py` | Builds a venv, runs `nd-prepare`, installs the result with a throwaway `nd_manager` profile, and checks the layout and every xsdir: a final newline, no blank lines, route `0`, and a file that exists for each entry. |
| `validate_md5.py` | Compares every installed file with the members of the vendor archives, and with the copy under `work/` while it still exists (not after `--cleanup-work`). |
| `validate_xsdir.py` | Compares the installed xsdirs with the vendor originals and with the ACE headers. |
| `validate_downloads_md5.py` | Compares files installed by `nd_manager` with its kept download archives. |
| `validation_common.py` | Helpers shared by the validators. |
| `run_verify_full.slurm` | Runs `verify_full_run.py`, `validate_md5.py` and `validate_xsdir.py --strict` on an offline SLURM node. |

Run the scripts from this directory or by path. Each one has `--help`.

## The oracle

`validation_expected.json` is maintained by hand, on purpose: it is not derived
from `sources.json`, so a bug in the tool cannot hide in the check. Under
`libraries`, each key is `<KEY>_<CATEGORY>` and may hold:

- `sources`: where the original vendor xsdir text is. `path` is a glob relative
  to `--workdir`; `member` is an fnmatch pattern inside the archive (leave it
  out for a loose file); `all_archives` allows the glob to match many archives,
  one fragment each; `placeholder_name` replaces the token `filename` by
  `<member stem>.ace`; `label` names the source in reports. The first source is
  the primary one. Later sources may add entries the first lacks, or correct
  entries it gets wrong.
- `ignore_file_case`: the vendor xsdir spells some file names in another case
  than the archive (FENDL-2.1).
- `rename_duplicated_names`: the vendor xsdir reuses a table name for many
  tables (JEFF-4.0 TSL), so the final xsdir may give them unique names.
- `expected`: counts seen in the reviewed full run, read by both validators. A
  difference is a warning, or an error with `--strict`. After checking why the
  counts moved, refresh them with `validate_xsdir.py --write-expected-counts`,
  which updates its own counts and keeps those of `validate_md5.py`.

## xsdir format

An entry has seven required fields: ZAID, atomic weight ratio, file, access
route, file type, address and table length. Record length, entries per record,
temperature and the `ptable` flag may be left out. This is the same rule
`src/nd_prepare/xsdir.py` uses; see LA-UR-25-26264 for the field list.
