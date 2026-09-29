#!/bin/sh
# Per-user install; no sudo, PATH edits, services or deletion of model/history.
set -eu
src=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
dst="${XDG_DATA_HOME:-$HOME/.local/share}/local-agent-app"
if [ "$src" = "$dst" ]; then
    printf '%s\n' 'Already installed in the target directory.'
    exit 0
fi
if [ -e "$dst" ]; then
    printf '%s\n' "Target already exists: $dst. Close the app and retain/rename that application folder before installing a new version. User data is stored separately."
    exit 1
fi
mkdir -p "$dst"
cp -R "$src/." "$dst/"
mkdir -p "$HOME/.local/share/applications"
if printf '%s' "$dst" | grep -q '["`$\\%]'; then
    printf '%s\n' "Installed to $dst; this path cannot be used safely for a desktop shortcut. Launch LocalAgent directly."
else
    cat > "$HOME/.local/share/applications/local-agent.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Local Agent
Exec="$dst/LocalAgent"
Terminal=false
Categories=Office;Utility;
EOF
fi
printf '%s\n' "Installed: $dst/LocalAgent"
