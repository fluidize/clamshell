#!/usr/bin/env bash
# Install a desktop launcher and .desktop entry for the GTK edition.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bin_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"
data_dir="${XDG_DATA_HOME:-$HOME/.local/share}"
app_dir="$data_dir/applications"
icon_dir="$data_dir/icons/hicolor/scalable/apps"

mkdir -p "$bin_dir" "$app_dir"

launcher="$bin_dir/clamshell-gtk"
cat > "$launcher" <<EOF
#!/usr/bin/env bash
exec "$root/run-gtk.sh" "\$@"
EOF
chmod +x "$launcher"

cp "$root/data/io.github.fluidize.Clamshell.desktop" "$app_dir/"
# GNOME Shell does not necessarily have ~/.local/bin in its PATH, so give
# the desktop entry the absolute launcher path instead of a bare command.
sed -i "s|^Exec=.*|Exec=\"$launcher\"|" \
    "$app_dir/io.github.fluidize.Clamshell.desktop"

# The app now uses the stock GNOME audio icon; drop any legacy custom icon.
rm -f "$icon_dir/io.github.fluidize.Clamshell.svg"

update-desktop-database "$app_dir" >/dev/null 2>&1 || true
gtk-update-icon-cache -f -t "$data_dir/icons/hicolor" >/dev/null 2>&1 || true

echo "Installed Clamshell for GTK:"
echo "  launcher: $launcher"
echo "  desktop:  $app_dir/io.github.fluidize.Clamshell.desktop"
echo "Launch it from your app grid or run: clamshell-gtk"
echo "The launcher refers to $root, so re-run this script if you move the repository."
echo "To undo this installation, run: $root/uninstall-gtk.sh"
