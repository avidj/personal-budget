#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Wipe the LOCAL stack's data (Actual budget, pipeline database, cached files)
# and recreate an empty budget with the given on-budget accounts.
# Usage: scripts/reset-local.sh [AccountName ...]   (default: Giro)
set -eu
cd "$(dirname "$0")/.."
printf 'This deletes all local Actual data and the pipeline database. Continue? [y/N] '
read -r answer
[ "$answer" = "y" ] || exit 1
docker compose down -v
docker compose up -d --build
args=""
for a in "${@:-Giro}"; do args="$args --account $a"; done
# shellcheck disable=SC2086
docker compose run --rm pipeline actual-setup $args
find inbox -type f ! -name .gitkeep -delete
echo "reset complete: Actual at http://localhost:5006, pipeline at http://127.0.0.1:8000"
