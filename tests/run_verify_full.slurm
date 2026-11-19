#!/bin/bash
# SPDX-FileCopyrightText: 2026 ENEA
# SPDX-FileContributor: Alberto P
#
# SPDX-License-Identifier: MPL-2.0
#
# SLURM submission script for the full nd_prepare + nd_manager verification
# (verify_full_run.py), for compute nodes with no network access. It then runs
# validate_md5.py and validate_xsdir.py on the same workdir (reports in
# $WORKDIR/validation/). A failing step does not skip the later ones; the job
# exits non-zero if any step failed.
#
# Prerequisite, once, on a host with network access: build venv/ and fill
# cache/ with every source, since a file missing from cache/ makes the offline
# job fail. The three D1SDATA zips go directly in <workdir>.
#
#   cd <workdir>
#   python3 /path/to/nd_prepare/tests/verify_full_run.py --workdir . \
#       --nd-prepare-path /path/to/nd_prepare --nd-manager-path /path/to/nd_manager \
#       --only IRDFF2:dosimetry
#   venv/bin/nd-prepare --all --download-only
#
# The first command builds venv/ and checks the setup on one small library; the
# second downloads the rest without building it, and stops if a D1SDATA zip is
# missing (then give it --only with the other keys). Running verify_full_run.py
# without --only instead does both and also builds everything once online.
#
# Then submit with the same paths (ND_MANAGER_PATH can be left out when
# nd_manager is the sibling checkout next to nd_prepare):
#
#   sbatch --partition=<partition> \
#       --export=ALL,WORKDIR=/scratch/me/nd_test,ND_PREPARE_PATH=/path/to/nd_prepare,ND_MANAGER_PATH=/path/to/nd_manager \
#       run_verify_full.slurm
#
# WORKDIR must be the same path on the build host and the compute node: venvs
# embed absolute paths.

#SBATCH --job-name=nd_verify_full
#SBATCH --cpus-per-task=1
#SBATCH --mem=16G
#SBATCH --time=6:00:00
#SBATCH --output=%x-%j.out
#SBATCH --error=%x-%j.err

set -euo pipefail

# Adjust to whatever module provides python3 on the HPC cluster; ideally the
# same interpreter (version/path) that was loaded when venv/ was built above,
# since a venv is tied to the python3 that created it.
# module load python/3.x

# Set at submission time (see above); ND_MANAGER_PATH defaults to the
# sibling checkout next to nd_prepare.
WORKDIR="${WORKDIR:?set WORKDIR to the verification workdir}"
ND_PREPARE_PATH="${ND_PREPARE_PATH:?set ND_PREPARE_PATH to the nd_prepare checkout}"
ND_MANAGER_PATH="${ND_MANAGER_PATH:-$ND_PREPARE_PATH/../nd_manager}"

# The venv is pre-built: keep pip off the network, and fail fast if it tries.
export PIP_NO_INDEX=1
export PIP_RETRIES=0
export PIP_TIMEOUT=5
# Without proxies, a source missing from cache/ fails at once instead of hanging.
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY

status=0

python3 "$ND_PREPARE_PATH/tests/verify_full_run.py" --workdir "$WORKDIR" \
    --nd-prepare-path "$ND_PREPARE_PATH" \
    --nd-manager-path "$ND_MANAGER_PATH" || status=1

# Independent validation of the installed result (see tests/validate_*.py).
JOBS="${SLURM_CPUS_PER_TASK:-1}"
python3 "$ND_PREPARE_PATH/tests/validate_md5.py" --workdir "$WORKDIR" --jobs "$JOBS" || status=1
python3 "$ND_PREPARE_PATH/tests/validate_xsdir.py" --workdir "$WORKDIR" --strict || status=1

exit "$status"
