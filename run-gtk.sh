#!/usr/bin/env bash
# Launch the GTK 4 / libadwaita edition of Clamshell.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venv="$root/.venv-gtk"

if [ ! -x "$venv/bin/python" ]; then
    /usr/bin/python3 -m venv --system-site-packages "$venv"
fi

if ! "$venv/bin/python" -c "import gi" >/dev/null 2>&1; then
    echo "PyGObject is not available. Install python3-gi, gir1.2-gtk-4.0 and gir1.2-adw-1." >&2
    exit 1
fi

if ! "$venv/bin/python" -c "import mutagen, PIL" >/dev/null 2>&1; then
    "$venv/bin/pip" install --quiet -r "$root/requirements-gtk.txt"
fi

cd "$root"
exec "$venv/bin/python" -m clamshell_gtk "$@"
