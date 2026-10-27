#!/usr/bin/env bash
# Remove the desktop launcher, .desktop entry and any legacy custom icon
# installed by install-gtk.sh. Pass --purge to also delete .venv-gtk.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bin_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"
data_dir="${XDG_DATA_HOME:-$HOME/.local/share}"
app_dir="$data_dir/applications"
icon_dir="$data_dir/icons/hicolor/scalable/apps"

targets=(
    "$bin_dir/clamshell-gtk"
    "$app_dir/io.github.fluidize.Clamshell.desktop"
    "$icon_dir/io.github.fluidize.Clamshell.svg"
)

removed=0
for target in "${targets[@]}"; do
    if [ -e "$target" ]; then
        rm -f "$target"
        echo "Removed $target"
        removed=1
    fi
done

if [ "${1:-}" = "--purge" ]; then
    if [ -d "$root/.venv-gtk" ]; then
        rm -rf "$root/.venv-gtk"
        echo "Removed $root/.venv-gtk"
        removed=1
    fi
fi

update-desktop-database "$app_dir" >/dev/null 2>&1 || true
gtk-update-icon-cache -f -t "$data_dir/icons/hicolor" >/dev/null 2>&1 || true

if [ "$removed" -eq 0 ]; then
    echo "Nothing to remove."
else
    echo "Clamshell's desktop integration has been removed."
fi
