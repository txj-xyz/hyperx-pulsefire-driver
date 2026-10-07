# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Userspace Linux driver + PyQt6 GUI for the HyperX Pulsefire Haste 2 Pro 4K mouse (`03f0:0cbd` wireless dongle, `03f0:0abd` USB cable). No kernel module: it reads/writes 64-byte HID reports on the mouse's vendor interface via `/dev/hidraw*`. Only runtime dependency is PyQt6 (also used for D-Bus on KDE). There are no tests, linter config, or CI.

## Commands

Run from the source tree (needs the udev rule installed, or root, to open hidraw):

    python3 -m hyperx_haste2                 # GUI (no subcommand = GUI; `gui` also works)
    python3 -m hyperx_haste2 status
    python3 -m hyperx_haste2 probe           # read-only raw dump of every known query; use first when debugging
    python3 -m hyperx_haste2 set --dpi 400 800 1600 3200 --stage 2 --polling 4000 [--save]
    python3 -m hyperx_haste2 set --color 00ffcc --brightness 60
    python3 -m hyperx_haste2 set --accel flat --accel-speed 0

Install/packaging:

    ./install.sh [--dry-run|--uninstall|-y]      # Arch: builds+installs pacman pkg; others: /usr/local + /etc/udev/rules.d
    cd packaging/arch && ./make-tarball.sh && makepkg -si

Testing changes requires the physical mouse. `set` without `--save` is volatile (lost on power-off); `--save` writes the onboard profile, so prefer testing without it.

## Architecture

- `protocol.py` — everything that touches the device. The module docstring is the authoritative protocol reference (report IDs, packet layouts, polling/DPI encodings); keep it updated when adding commands. Key points:
  - `find_config_node()` picks the vendor (usage page 0xFF00) interface by scanning `/sys/class/hidraw` descriptors.
  - Requests use report ID N; the mouse replies on N+1, or with a generic `ff 01` ACK (bytes 14/15/16 = rid/sub/result). `Device.transact()` handles both, and raises `DeviceError` with "mouse may be asleep" on timeout.
  - `fb 08` reports are async DPI-button notifications; `_note()` swallows them during any read and queues them in `dpi_stage_events`, which the GUI polls.
  - `set_dpi_table()` writes then reads back and raises on mismatch. DPI/polling/stage/stage colours are one combined table (`32 01`/`32 02`), so changing any one field means read-modify-write of the whole table.
  - `Device` is not thread-safe.
- `desktop.py` — pointer acceleration, which is a libinput/desktop setting, not stored in the mouse. KDE: per-device via KWin D-Bus (`org.kde.KWin` InputDevice) plus persisted with `kwriteconfig6` into `kcminputrc` under `[Libinput][vendor][product][name]`. GNOME: global `gsettings` mouse keys. Other desktops raise `DesktopError`.
- `cli.py` — argparse front end; `main()` is the `hyperx-haste2` entry point.
- `gui.py` — `Worker` owns the single `Device` on a background thread with a job queue so a sleeping/unplugged mouse never blocks the UI; it reopens the device after `OSError`. All device access from the GUI must go through `worker.submit(fn, callback)`. Timers poll battery (60 s) and DPI-button events (1 s).

## Gotchas when adding/renaming files or bumping versions

- Module files are listed explicitly in `packaging/arch/make-tarball.sh` and in `install_app_generic()` in `install.sh`; a new `.py` file must be added to both.
- Version lives in `pyproject.toml`, `hyperx_haste2/__init__.py`, and `packaging/arch/PKGBUILD` (`pkgver`).
- The PKGBUILD `url` is still a `CHANGEME` placeholder.

## Not implemented

Button remapping, macros, lift-off distance — the protocol for these is unknown and would need NGENUITY USB captures. (NGENUITY under Proton needs `PROTON_ENABLE_HIDRAW=0x03F0/0x0CBD`.)
