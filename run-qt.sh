#!/usr/bin/env bash
# Launch the legacy Qt / PySide6 edition of Clamshell.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venv="$root/.venv"

if [ ! -x "$venv/bin/python" ]; then
    /usr/bin/python3 -m venv "$venv"
fi

if ! "$venv/bin/python" -c "import PySide6, mutagen, PIL" >/dev/null 2>&1; then
    "$venv/bin/pip" install --quiet -r "$root/clamshell_qt/requirements.txt"
fi

cd "$root"
exec "$venv/bin/python" clamshell_qt/main.py "$@"
