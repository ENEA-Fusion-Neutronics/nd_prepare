<!--
SPDX-FileCopyrightText: 2026 ENEA
SPDX-FileContributor: Alberto P

SPDX-License-Identifier: MPL-2.0
-->

# Contributing

## Adding a nuclear data library

Adding a nuclear data library is almost always a `sources.json` change, with no
new code.

Start by looking at what the vendor actually ships: which categories exist
(`neutron`, `tsl`, `proton`, `dosimetry`), how the archives are laid out, and
where the xsdir lives. Check a few xsdir entries against their ACE headers, as
that is how most upstream defects get caught.

Then pick a build mode for each category: `ready` if one archive holds the data
and a complete xsdir, `fragments` if you have to concatenate per-isotope xsdir
pieces, `merge-master` if several archives share a separate master xsdir. The
closest existing library is usually the best template, so copy its entry and
adapt it. The README documents every field.

A few rules:

- The library key is letters and digits only.
- Every source needs an `md5`. Use the vendor's checksum when there is one,
  otherwise download twice and compare.
- `url` sources must be openly published. Anything restricted goes in as a
  `path` source and is never committed.
- Work around upstream quirks with manifest fields (`flatten_dirs`,
  `drop_members`, `rename_xsdir_files`, and so on), not with code.

Try it with `nd-prepare --only NEWKEY`. The manifest is validated before any
download, and the run warns about duplicate table names, so read the warnings.

Before opening a pull request:

1. Add the library to the tables in `README.md` (libraries, per-library fixes,
   size, and license).
2. Add its expected results to `tests/validation_expected.json` by hand, and run
   `tests/verify_full_run.py --only NEWKEY`, then `tests/validate_md5.py` and
   `tests/validate_xsdir.py`.
3. Run `ruff check .` and `reuse lint`.
