#!/usr/bin/env bash
# Install the `lc` and `lc-gui` commands and an app-menu entry for the current user.
# Nothing is copied: the commands link back to this folder, so `git pull` updates them.
#
#   ./install.sh              install
#   ./install.sh --uninstall  remove what install created
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bin="${XDG_BIN_HOME:-$HOME/.local/bin}"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
desktop="$apps/lc-solutions.desktop"

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -f "$bin/lc" "$bin/lc-gui" "$desktop"
    echo "Removed lc, lc-gui and the app-menu entry."
    exit 0
fi

mkdir -p "$bin" "$apps"
chmod +x "$here/lc.py" "$here/lc_gui.py"
ln -sf "$here/lc.py" "$bin/lc"
ln -sf "$here/lc_gui.py" "$bin/lc-gui"

cat > "$desktop" <<EOF
[Desktop Entry]
Type=Application
Name=LeetCode Solutions
Comment=Find a LeetCode solution and copy it to the clipboard
Exec="$here/lc_gui.py"
Icon=accessories-text-editor
Terminal=false
Categories=Development;
EOF

echo "Installed: $bin/lc, $bin/lc-gui and the \"LeetCode Solutions\" app-menu entry."

case ":$PATH:" in
    *":$bin:"*) ;;
    *) echo "Note: $bin is not on your PATH. Add it to run \`lc\` and \`lc-gui\` from anywhere." ;;
esac

if ! python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")' 2>/dev/null; then
    echo "Note: the GUI needs PyGObject, GTK 4 and libadwaita (the \`lc\` command works without them)."
fi
if ! command -v wl-copy >/dev/null && ! command -v xclip >/dev/null && ! command -v xsel >/dev/null; then
    echo "Note: no clipboard tool found. Install wl-clipboard (Wayland) or xclip (X11)."
fi
