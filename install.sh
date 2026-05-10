#!/bin/sh
set -e

# Detect session type
SESSION="${XDG_SESSION_TYPE:-}"
DESKTOP="${XDG_CURRENT_DESKTOP:-}"
IS_HYPRLAND=0
[ -n "$HYPRLAND_INSTANCE_SIGNATURE" ] && IS_HYPRLAND=1
case "$DESKTOP" in *[Hh]yprland*) IS_HYPRLAND=1 ;; esac

if [ "$IS_HYPRLAND" = "0" ]; then
    # Install xrandr if not present (needed for X11/KDE)
    if ! command -v xrandr >/dev/null 2>&1; then
        echo "Installing xrandr..."
        if command -v pacman >/dev/null 2>&1; then
            sudo pacman -Sy --noconfirm xorg-xrandr
        elif command -v apt-get >/dev/null 2>&1; then
            sudo apt-get install -y x11-xserver-utils
        elif command -v dnf >/dev/null 2>&1; then
            sudo dnf install -y xrandr
        else
            echo "Warning: could not install xrandr automatically. Please install it manually."
        fi
    fi
fi

# Install pipx if not present
if ! command -v pipx >/dev/null 2>&1; then
    echo "Installing pipx..."
    python3 -m pip install --user pipx
    python3 -m pipx ensurepath
    export PATH="$PATH:$HOME/.local/bin"
fi

# Install or upgrade the tool (--force re-fetches from git every time)
pipx install --force git+https://github.com/ZoeWithTheE/easy-wallpaper-span

if [ "$IS_HYPRLAND" = "1" ]; then
    # Hyprland autostart
    echo ""
    echo "Hyprland detected."
    echo "Make sure 'hyprpaper' or 'swww' is installed and running."
    echo ""
    echo "To restore your wallpaper on login, add this to your hyprland.conf:"
    echo "  exec-once = sleep 3 && easy-wallpaper-span restore"
    echo ""
    echo "If using hyprpaper, also add:"
    echo "  exec-once = hyprpaper"
else
    # KDE autostart
    mkdir -p "$HOME/.config/autostart"
    AUTOSTART="$HOME/.config/autostart/easy-wallpaper-span.desktop"
    cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=Easy Wallpaper Span
Exec=sh -c 'sleep 10 && easy-wallpaper-span restore'
Hidden=false
X-KDE-autostart-phase=2
EOF
    echo "KDE autostart registered."
fi

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
