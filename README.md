# easy-wallpaper-span

Span a single image across multiple monitors in **KDE Plasma** (X11), with a GUI to position and crop per-monitor.

![screenshot placeholder]

## Requirements

These must be installed on your system:

| Dependency | Package name (Ubuntu/Debian) | Arch | Fedora |
|---|---|---|---|
| xrandr | `x11-xserver-utils` | `xorg-xrandr` | `xorg-x11-server-utils` |
| ImageMagick 7 | `imagemagick` | `imagemagick` | `ImageMagick` |
| qdbus6 | `qt6-tools` | `qt6-tools` | `qt6-qttools` |

KDE Plasma 6 on X11 is required.

## Install

```sh
curl -sSL https://raw.githubusercontent.com/ZoeWithTheE/easy-wallpaper-span/main/install.sh | sh
```

Then run:

```sh
easy-wallpaper-span
```

### Manual install (pipx)

```sh
pipx install git+https://github.com/ZoeWithTheE/easy-wallpaper-span
```

### Uninstall

```sh
pipx uninstall easy-wallpaper-span
```

## Usage

1. Click **Browse** to pick an image
2. Drag the image to pan, drag monitor corners to adjust crop areas
3. Use **Resize mode** dropdown to control corner-drag behaviour:
   - **Respect Aspect Ratio** — uniform scale, keeps physical display ratio
   - **Adjust Face** — stretch one axis at a time
   - **Adjust Corner** — free stretch
4. Click **▶ Apply** to set the wallpaper across all screens
5. Press **F** to fit the canvas back to the window

**Calibration mode** (in the sidebar) overlays a colour grid so you can check alignment across screens before committing to an image.

Settings (monitor layout, last image) are saved automatically to `~/.local/share/wallpaper-span/`.
