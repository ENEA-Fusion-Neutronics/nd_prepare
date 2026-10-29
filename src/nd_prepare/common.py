# SPDX-FileCopyrightText: 2026 ENEA
# SPDX-FileContributor: Alberto P
#
# SPDX-License-Identifier: MPL-2.0

"""Constants and logging helpers shared by the nd_prepare modules."""

import textwrap
from datetime import date, datetime, time, timezone
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
CATEGORIES = ("neutron", "tsl", "proton", "dosimetry")


def date_to_epoch(text):
    """Seconds since the epoch of midnight UTC on a YYYY-MM-DD date; ValueError otherwise."""
    return int(datetime.combine(date.fromisoformat(text), time(), tzinfo=timezone.utc).timestamp())


def log(msg):
    print(f"[prepare] {msg}", flush=True)


def log_wrapped(items, indent="    "):
    """Log items space-separated, wrapped to 100 columns."""
    for line in textwrap.wrap(" ".join(items), width=100):
        log(f"{indent}{line}")


def plural_entries(count):
    return "entry" if count == 1 else "entries"
