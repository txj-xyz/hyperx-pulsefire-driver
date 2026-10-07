"""Pointer acceleration, which is a desktop (libinput) setting rather than a mouse setting.

KDE Plasma (Wayland): applied live to this mouse only through KWin's D-Bus InputDevice
interface, and saved to ~/.config/kcminputrc under [Libinput][vendor][product][name],
the same place System Settings > Mouse writes, so it survives reboots and replugs.

GNOME: there is no per-device setting, so the global mouse keys in gsettings are used.

Profiles: "flat" = constant speed factor (1:1 with the mouse's DPI when speed is 0);
"adaptive" = libinput's default curve, faster movements travel further.
Speed is libinput's pointer speed, -1.0 .. 1.0, 0 = default.
"""
from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess

from . import protocol as p

PROFILES = ('flat', 'adaptive')
_KDE_PROFILE_CODE = {'flat': 1, 'adaptive': 2}  # libinput enum values KWin stores in kcminputrc


class DesktopError(RuntimeError):
    pass


@dataclasses.dataclass
class Accel:
    profile: str
    speed: float

    def __post_init__(self):
        if self.profile not in PROFILES:
            raise ValueError(f'profile must be one of {PROFILES}')
        if not -1.0 <= self.speed <= 1.0:
            raise ValueError('speed must be between -1.0 and 1.0')


def backend() -> str | None:
    desktop = os.environ.get('XDG_CURRENT_DESKTOP', '').lower()
    if 'kde' in desktop:
        return 'kde'
    if 'gnome' in desktop and shutil.which('gsettings'):
        return 'gnome'
    return None


# --- KDE --------------------------------------------------------------------
def _kde_devices():
    """(sysName, properties) for each pointer device belonging to this mouse."""
    from PyQt6.QtDBus import QDBusConnection, QDBusInterface
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        raise DesktopError('no D-Bus session')

    def props(path, iface):
        reply = QDBusInterface('org.kde.KWin', path, 'org.freedesktop.DBus.Properties', bus).call('GetAll', iface)
        args = reply.arguments()
        if not args:
            raise DesktopError(f'KWin did not answer ({reply.errorMessage() or "is this a Plasma Wayland session?"})')
        return args[0]

    names = props('/org/kde/KWin/InputDevice', 'org.kde.KWin.InputDeviceManager').get('devicesSysNames', [])
    found = []
    for name in names:
        d = props(f'/org/kde/KWin/InputDevice/{name}', 'org.kde.KWin.InputDevice')
        if d.get('vendor') == p.VID and d.get('product') in p.PIDS and d.get('pointer') \
                and d.get('supportsPointerAcceleration'):
            found.append((name, d))
    if not found:
        raise DesktopError('the mouse is not connected (KDE lists no matching pointer device)')
    return found


def _kde_set(sysname: str, prop: str, value) -> None:
    from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusVariant
    iface = QDBusInterface('org.kde.KWin', f'/org/kde/KWin/InputDevice/{sysname}',
                           'org.freedesktop.DBus.Properties', QDBusConnection.sessionBus())
    reply = iface.call('Set', 'org.kde.KWin.InputDevice', prop, QDBusVariant(value))
    if reply.errorMessage():
        raise DesktopError(f'KWin refused {prop}: {reply.errorMessage()}')


def _kde_read() -> Accel:
    _, d = _kde_devices()[0]
    return Accel('flat' if d['pointerAccelerationProfileFlat'] else 'adaptive', float(d['pointerAcceleration']))


def _kde_apply(accel: Accel) -> str:
    if not shutil.which('kwriteconfig6'):
        raise DesktopError('kwriteconfig6 not found (needed to save the setting)')
    for sysname, d in _kde_devices():
        _kde_set(sysname, 'pointerAccelerationProfileFlat', accel.profile == 'flat')
        _kde_set(sysname, 'pointerAccelerationProfileAdaptive', accel.profile == 'adaptive')
        _kde_set(sysname, 'pointerAcceleration', float(accel.speed))
        group = ['--group', 'Libinput', '--group', str(d['vendor']), '--group', str(d['product']),
                 '--group', d['name']]
        for key, value in (('PointerAccelerationProfile', str(_KDE_PROFILE_CODE[accel.profile])),
                           ('PointerAcceleration', f'{accel.speed:.3f}')):
            subprocess.run(['kwriteconfig6', '--file', 'kcminputrc', *group, '--key', key, value],
                           check=True, capture_output=True)
    return 'applied to this mouse in KDE and saved'


# --- GNOME ------------------------------------------------------------------
_GNOME_SCHEMA = 'org.gnome.desktop.peripherals.mouse'


def _gsettings(*args: str) -> str:
    return subprocess.run(['gsettings', *args], check=True, capture_output=True, text=True).stdout.strip()


def _gnome_read() -> Accel:
    profile = _gsettings('get', _GNOME_SCHEMA, 'accel-profile').strip("'")
    return Accel('flat' if profile == 'flat' else 'adaptive', float(_gsettings('get', _GNOME_SCHEMA, 'speed')))


def _gnome_apply(accel: Accel) -> str:
    _gsettings('set', _GNOME_SCHEMA, 'accel-profile', accel.profile)
    _gsettings('set', _GNOME_SCHEMA, 'speed', str(accel.speed))
    return 'applied and saved in GNOME (affects all mice: GNOME has no per-mouse setting)'


# --- public -----------------------------------------------------------------
def _dispatch(kde, gnome):
    b = backend()
    if b == 'kde':
        return kde
    if b == 'gnome':
        return gnome
    raise DesktopError('pointer acceleration is only supported on KDE Plasma and GNOME '
                       f'(this desktop: {os.environ.get("XDG_CURRENT_DESKTOP") or "unknown"})')


def read() -> Accel:
    return _dispatch(_kde_read, _gnome_read)()


def apply(accel: Accel) -> str:
    return _dispatch(_kde_apply, _gnome_apply)(accel)
