# SPDX-FileCopyrightText: 2026 ENEA
# SPDX-FileContributor: Alberto P
#
# SPDX-License-Identifier: MPL-2.0

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("nd-prepare")
except PackageNotFoundError:
    __version__ = "unknown"
