# SPDX-FileCopyrightText: 2026 ENEA
# SPDX-FileContributor: Alberto P
#
# SPDX-License-Identifier: MPL-2.0

"""Reproducible packing of a payload directory into a .tar.gz or .tar.xz."""

import gzip
import os
import tarfile

# Low compression levels: ACE data is dense ASCII floats, so higher levels
# cost much more CPU for nearly the same size.
XZ_PRESET = 1
GZ_LEVEL = 6


def pack_payload(payload_dir, root_dir_name, output_archive, compression, mtime):
    """Pack payload_dir; every entry gets the fixed mtime (epoch seconds), no owner
    and a umask-independent mode, so identical payloads pack to identical bytes."""

    def normalize(info):
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        info.mtime = mtime
        info.mode = 0o755 if info.isdir() else 0o644
        return info

    output_archive.parent.mkdir(parents=True, exist_ok=True)
    # Written as .part and renamed, so an interrupted run leaves no truncated archive.
    part = output_archive.with_name(output_archive.name + ".part")
    if compression == "xz":
        with tarfile.open(part, mode="w:xz", preset=XZ_PRESET) as tf:
            tf.add(payload_dir, arcname=root_dir_name, filter=normalize)
    else:
        # GzipFile directly, because tarfile's "w:gz" stamps the current time in
        # the header; filename keeps ".part" out of the header's name field.
        with open(part, "wb") as raw, \
                gzip.GzipFile(output_archive.name, "wb", GZ_LEVEL, raw, mtime) as gz, \
                tarfile.open(fileobj=gz, mode="w") as tf:
            tf.add(payload_dir, arcname=root_dir_name, filter=normalize)
    os.replace(part, output_archive)
