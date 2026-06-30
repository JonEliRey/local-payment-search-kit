#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  . .venv/bin/activate
fi

exec payment-search start "$@"
