# HyperX Pulsefire Haste 2 Pro 4K: Linux control

Userspace driver + PyQt6 GUI for `03f0:0cbd` (dongle) / `03f0:0abd` (cable).
No kernel module needed: it talks to the mouse's vendor HID interface via `/dev/hidraw*`.

## Install
    ./install.sh                # Arch, Debian/Ubuntu, Fedora, openSUSE
    ./install.sh --uninstall
    ./install.sh --dry-run      # preview every step without changing anything

Run it as your normal user; it asks for your password once. On Arch it builds and installs
a real pacman package; elsewhere it installs into /usr/local and /etc/udev/rules.d.

### Manual Arch package build
    cd packaging/arch
    ./make-tarball.sh
    makepkg -si

This installs the `hyperx-haste2` command, a "HyperX Haste 2 Pro" app-menu entry and the
udev rule. Replug the dongle afterwards. Uninstall with `sudo pacman -R hyperx-haste2`.

### Without packaging (any distro)
    sudo cp udev/70-hyperx-pulsefire-haste2-pro.rules /etc/udev/rules.d/
    sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=hidraw --subsystem-match=input
    pipx install .        # needs PyQt6 and python-evdev

## Use
    hyperx-haste2                       # GUI (or python3 -m hyperx_haste2 from the source tree)
    python3 -m hyperx_haste2 status
    python3 -m hyperx_haste2 set --dpi 400 800 1600 3200 --stage 2 --polling 4000
    hyperx-haste2 set --color 00ffcc --brightness 60
    hyperx-haste2 set --accel flat --accel-speed 0
    python3 -m hyperx_haste2 probe      # read-only raw dump, for debugging
    hyperx-haste2 remap                 # run the button bindings in the foreground

"Apply" is volatile (lost on power-off); "Save to mouse" / `--save` writes the onboard profile.

## Supported
Battery (%, charging state, voltage, temperature), 5 DPI stages (50-26000, step 50) with
indicator colours, active stage (tracks the DPI button), polling 125-4000 Hz (cable: max 1000),
LED colour + brightness, and pointer acceleration (flat/adaptive + speed). Acceleration is a
desktop setting, not stored in the mouse: on KDE Plasma it is set for this mouse only and saved in
`~/.config/kcminputrc` (same as System Settings > Mouse); on GNOME it uses the global mouse settings. Not yet: on-board button remap, macros, lift-off distance (need NGENUITY USB captures).

## Button bindings
The **Bindings** tab binds left/right/middle/back/forward and wheel up/down to another mouse
button, a scroll, a keyboard shortcut (click the key field and press the combo; right-click it
for media keys), or nothing. It is done in software because the on-board remap protocol is
unknown: a background process (`hyperx-haste2 remap`, started by the tab) grabs the mouse's
input device and re-emits events through uinput as a clone of the mouse (same name and IDs,
so per-mouse desktop settings such as KDE acceleration still apply) plus a small virtual
keyboard. Bindings live in `~/.config/hyperx-haste2/bindings.json` and apply live. There is no
on/off switch: while any binding is set the remapper runs and is added to login autostart;
resetting everything to Default stops it and removes the autostart entry. The DPI button is
handled by the firmware and cannot be rebound. Needs the udev rule (it grants access to the
mouse's input node and to `/dev/uinput`, like Steam's rules do); log out and in after installing.

Protocol notes are at the top of `hyperx_haste2/protocol.py`.

## NGENUITY under Proton
Proton ignores HID mice by default. Launch it with
`PROTON_ENABLE_HIDRAW=0x03F0/0x0CBD` in the environment and fully restart the prefix.
