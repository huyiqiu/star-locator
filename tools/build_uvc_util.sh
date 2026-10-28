#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$(uname -s)" != Darwin ]; then
  echo 'UVC helper build is supported on macOS only.' >&2
  exit 1
fi

mkdir -p "$PROJECT_ROOT/bin"
clang -o "$PROJECT_ROOT/bin/uvc-util" -framework IOKit -framework Foundation \
  "$PROJECT_ROOT/vendor/uvc-util/src/uvc-util.m" \
  "$PROJECT_ROOT/vendor/uvc-util/src/UVCController.m" \
  "$PROJECT_ROOT/vendor/uvc-util/src/UVCType.m" \
  "$PROJECT_ROOT/vendor/uvc-util/src/UVCValue.m"
echo "Built $PROJECT_ROOT/bin/uvc-util"
