#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -e
if [ "$1" = "serve" ]; then
  pipeline migrate
  exec pipeline serve --host 0.0.0.0 --port 8000
fi
exec pipeline "$@"
