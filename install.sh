#!/bin/sh
set -e

# Install pipx if not present
if ! command -v pipx >/dev/null 2>&1; then
    echo "pipx not found, installing..."
    python3 -m pip install --user pipx
    python3 -m pipx ensurepath
    export PATH="$PATH:$HOME/.local/bin"
fi

pipx install git+https://github.com/ZoeWithTheE/easy-wallpaper-span

echo ""
echo "Installed. Run: easy-wallpaper-span"
echo "If the command is not found, restart your shell or run: source ~/.bashrc"
