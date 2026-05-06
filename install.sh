#!/bin/sh
set -e

# Install xrandr if not present
if ! command -v xrandr >/dev/null 2>&1; then
    echo "Installing xrandr..."
    if command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --noconfirm xorg-xrandr
    elif command -v apt-get >/dev/null 2>&1; then
        sudo apt-get install -y x11-xserver-utils
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y xrandr
    else
        echo "Warning: could not install xrandr automatically. Please install it manually for your distro."
    fi
fi

# Install pipx if not present
if ! command -v pipx >/dev/null 2>&1; then
    echo "Installing pipx..."
    python3 -m pip install --user pipx
    python3 -m pipx ensurepath
    export PATH="$PATH:$HOME/.local/bin"
fi

# Install or upgrade the tool
if pipx list | grep -q easy-wallpaper-span; then
    pipx upgrade easy-wallpaper-span
else
    pipx install git+https://github.com/ZoeWithTheE/easy-wallpaper-span
fi

# Register KDE autostart so the wallpaper is restored on login
mkdir -p "$HOME/.config/autostart"
AUTOSTART="$HOME/.config/autostart/easy-wallpaper-span.desktop"
cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=Easy Wallpaper Span
Exec=sh -c 'sleep 5 && easy-wallpaper-span restore'
Hidden=false
X-KDE-autostart-phase=2
EOF

echo ""
echo "Done. Run: easy-wallpaper-span"
echo "Wallpaper will restore automatically on login."
echo ""
echo "Commands:"
echo "  easy-wallpaper-span                   open GUI"
echo "  easy-wallpaper-span apply IMAGE       apply without GUI"
echo "  easy-wallpaper-span apply -p NAME     apply a saved profile"
echo "  easy-wallpaper-span restore           re-apply last wallpaper"
echo "  easy-wallpaper-span profiles          list saved profiles"
echo "  easy-wallpaper-span save NAME         save current state as profile"
echo "  easy-wallpaper-span delete NAME       delete a profile"
