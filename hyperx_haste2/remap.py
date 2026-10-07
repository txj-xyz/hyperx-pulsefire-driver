"""Button bindings, done in userspace because the mouse's on-board remap protocol is unknown.

A small background process (`hyperx-haste2 remap`) grabs the mouse's evdev pointer node so
the desktop no longer sees it directly, and re-emits everything through two uinput devices:
  * a pointer that clones the mouse's name/vendor/product, so desktop per-mouse settings
    (e.g. KDE pointer acceleration saved in kcminputrc) still apply to it, and
  * a keyboard, for bindings that press keys.
Bound buttons/wheel directions are translated on the way through; everything else passes
through untouched. The DPI button is handled by the mouse's firmware and cannot be bound.

Config: ~/.config/hyperx-haste2/bindings.json
  {"bindings": {"BTN_SIDE": {"type": "keys", "keys": ["KEY_LEFTCTRL", "KEY_C"]}}}
Action types: "default", "disabled", "button" (+ "button": "BTN_..."), "scroll" (+ "dir": "up"/"down"),
"keys" (+ "keys": [...], pressed in order, released in reverse).
The process re-reads the config whenever it changes and exits once no binding is left. The GUI
starts it, and adds it to login autostart, whenever at least one binding is set.
"""
from __future__ import annotations

import fcntl
import json
import os
import select
import shlex
import sys
import time
from pathlib import Path

from . import protocol as p

CONFIG_DIR = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'hyperx-haste2'
CONFIG = CONFIG_DIR / 'bindings.json'
AUTOSTART = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'autostart' / 'hyperx-haste2-remap.desktop'
LOCK = Path(os.environ.get('XDG_RUNTIME_DIR') or f'/tmp/hyperx-haste2-{os.getuid()}') / 'hyperx-haste2-remap.lock'
LOG = LOCK.with_suffix('.log')

# Bindable inputs, in display order. Wheel directions are pseudo-inputs.
SOURCES = {
    'BTN_LEFT': 'Left button',
    'BTN_RIGHT': 'Right button',
    'BTN_MIDDLE': 'Middle button (wheel click)',
    'BTN_SIDE': 'Back side button',
    'BTN_EXTRA': 'Forward side button',
    'WHEEL_UP': 'Scroll wheel up',
    'WHEEL_DOWN': 'Scroll wheel down',
}
MOUSE_BUTTONS = {'BTN_LEFT': 'Left click', 'BTN_RIGHT': 'Right click', 'BTN_MIDDLE': 'Middle click',
                 'BTN_SIDE': 'Back', 'BTN_EXTRA': 'Forward'}

_PRETTY_KEYS = {
    'LEFTCTRL': 'Ctrl', 'RIGHTCTRL': 'Right Ctrl', 'LEFTSHIFT': 'Shift', 'RIGHTSHIFT': 'Right Shift',
    'LEFTALT': 'Alt', 'RIGHTALT': 'AltGr', 'LEFTMETA': 'Meta', 'RIGHTMETA': 'Right Meta',
    'ESC': 'Esc', 'PAGEUP': 'Page Up', 'PAGEDOWN': 'Page Down', 'VOLUMEUP': 'Volume Up',
    'VOLUMEDOWN': 'Volume Down', 'MUTE': 'Mute', 'PLAYPAUSE': 'Play/Pause', 'NEXTSONG': 'Next track',
    'PREVIOUSSONG': 'Previous track', 'STOPCD': 'Stop', 'SYSRQ': 'Print Screen',
}


_PHYS = 'hyperx-haste2-remap'  # marks our uinput devices


class RemapError(RuntimeError):
    pass


# --- config -----------------------------------------------------------------
def load() -> dict:
    try:
        cfg = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        cfg = {}
    cfg.pop('enabled', None)  # from older versions, which had an on/off switch
    cfg.setdefault('bindings', {})
    return cfg


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix('.tmp')
    tmp.write_text(json.dumps(cfg, indent=2) + '\n')
    tmp.replace(CONFIG)  # atomic, so the remapper never reads a half-written file


def key_label(name: str) -> str:
    short = name.removeprefix('KEY_').removeprefix('BTN_')
    return _PRETTY_KEYS.get(short, short.replace('_', ' ').title() if len(short) > 1 else short)


def describe(action: dict | None) -> str:
    kind = (action or {}).get('type', 'default')
    if kind == 'disabled':
        return 'Disabled'
    if kind == 'button':
        return MOUSE_BUTTONS.get(action.get('button'), action.get('button', '?'))
    if kind == 'scroll':
        return f'Scroll {action.get("dir", "up")}'
    if kind == 'keys':
        return '+'.join(key_label(k) for k in action.get('keys', [])) or 'No keys'
    return 'Default'


# --- process control (used by the GUI) ----------------------------------------
def is_running() -> bool:
    try:
        with open(LOCK, 'a') as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
            return False
    except BlockingIOError:
        return True
    except OSError:
        return False


def command() -> list[str]:
    # Run the same copy of the code as the caller (source tree or installed), not whatever
    # `hyperx-haste2` happens to be on PATH, which may be an older install.
    return ['env', f'PYTHONPATH={Path(__file__).resolve().parent.parent}', sys.executable, '-m', 'hyperx_haste2', 'remap']


def start() -> None:
    import subprocess
    if not is_running():
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, 'w') as log:
            subprocess.Popen(command(), start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=log, stderr=log)


def last_error() -> str | None:
    """The last line the remapper printed before it exited, if any."""
    try:
        lines = LOG.read_text().strip().splitlines()
    except OSError:
        return None
    return lines[-1] if lines else None


def autostart_enabled() -> bool:
    return AUTOSTART.exists()


def set_autostart(on: bool) -> None:
    if not on:
        AUTOSTART.unlink(missing_ok=True)
        return
    AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
    AUTOSTART.write_text(
        '[Desktop Entry]\nType=Application\nName=HyperX Haste 2 Pro button bindings\n'
        f'Exec={shlex.join(command())}\nIcon=hyperx-haste2\nNoDisplay=true\nX-GNOME-Autostart-enabled=true\n')


def check() -> str | None:
    """Why the remapper could not run right now, or None if it can."""
    try:
        import evdev  # noqa: F401
    except ImportError:
        return 'python-evdev is not installed (Arch: python-evdev, Debian/Fedora/openSUSE: python3-evdev).'
    if not os.access('/dev/uinput', os.W_OK):
        return ('No write access to /dev/uinput. Reinstall the udev rule (./install.sh) and log out and back '
                'in, or run: sudo modprobe uinput')
    devs = _find_mice()
    if not devs:
        return 'Mouse not found among input devices (is the dongle/cable plugged in?).'
    if not all(os.access(d, os.R_OK) for d in devs):
        return 'No read access to the mouse\'s input device. Reinstall the udev rule (./install.sh) and replug.'
    return None


# --- the remapper ---------------------------------------------------------------
def _find_mice() -> list[str]:
    """Event nodes of the mouse's pointer interface, read from sysfs.

    Opening every /dev/input node instead takes ~100 ms, which the remap loop runs every
    couple of seconds and which shows up as pointer stutter."""
    found = []
    for node in sorted(Path('/sys/class/input').glob('event*')):
        dev = node / 'device'
        try:
            vid = int((dev / 'id' / 'vendor').read_text(), 16)
            pid = int((dev / 'id' / 'product').read_text(), 16)
            if vid != p.VID or pid not in p.PIDS:
                continue
            if (dev / 'phys').read_text().strip() == _PHYS:  # our own virtual clone has the same ids
                continue
            words = (dev / 'capabilities' / 'key').read_text().split()
        except (OSError, ValueError):
            continue
        keys = int(''.join(w.zfill(16) for w in words), 16)  # sysfs: 64-bit words, most significant first
        if keys >> 272 & 1:  # BTN_LEFT
            found.append(f'/dev/input/{node.name}')
    return found


class _Remapper:
    def __init__(self, path: str):
        import evdev
        from evdev import ecodes as e
        self.e = e
        self.dev = evdev.InputDevice(path)
        caps, info = self.dev.capabilities(), self.dev.info
        buttons = sorted(set(caps.get(e.EV_KEY, [])) | {e.ecodes[b] for b in MOUSE_BUTTONS})
        rels = sorted(set(caps.get(e.EV_REL, [])) | {e.REL_WHEEL, e.REL_WHEEL_HI_RES})
        # Same name/ids as the real mouse so per-device desktop settings keep applying.
        self.pointer = evdev.UInput({e.EV_KEY: buttons, e.EV_REL: rels}, name=self.dev.name,
                                    vendor=info.vendor, product=info.product, version=info.version,
                                    bustype=info.bustype, phys=_PHYS)
        self.keyboard = evdev.UInput({e.EV_KEY: [c for c in e.KEY if 0 < c < e.BTN_MISC]},
                                     name=f'{self.dev.name} bindings', vendor=info.vendor,
                                     product=info.product, bustype=info.bustype, phys=_PHYS)
        self.bindings: dict = {}
        self.pressed: dict[int, dict] = {}  # source button -> the action its press started
        self.dev.grab()

    def close(self):
        for dev in (self.pointer, self.keyboard, self.dev):
            try:
                dev.close()  # closing the grabbed node also releases the grab
            except OSError:
                pass

    def _source(self, code: int) -> str | None:
        names = self.e.BTN.get(code, ())
        return next((n for n in ([names] if isinstance(names, str) else names) if n in SOURCES), None)

    def _key(self, dev, code: int, value: int):
        dev.write(self.e.EV_KEY, code, value)
        dev.syn()

    def _press(self, action: dict):
        e = self.e
        if action['type'] == 'button':
            self._key(self.pointer, e.ecodes[action['button']], 1)
        elif action['type'] == 'scroll':
            step = 1 if action.get('dir', 'up') == 'up' else -1
            self.pointer.write(e.EV_REL, e.REL_WHEEL, step)
            self.pointer.write(e.EV_REL, e.REL_WHEEL_HI_RES, step * 120)
            self.pointer.syn()
        elif action['type'] == 'keys':
            for k in action.get('keys', []):
                if k in e.ecodes:
                    self._key(self.keyboard, e.ecodes[k], 1)

    def _release(self, action: dict):
        e = self.e
        if action['type'] == 'button':
            self._key(self.pointer, e.ecodes[action['button']], 0)
        elif action['type'] == 'keys':
            for k in reversed(action.get('keys', [])):
                if k in e.ecodes:
                    self._key(self.keyboard, e.ecodes[k], 0)

    def handle(self, ev):
        e = self.e
        if ev.type == e.EV_KEY and (src := self._source(ev.code)):
            if ev.value == 1:
                action = self.bindings.get(src, {'type': 'default'})
                self.pressed[ev.code] = action
            else:
                # Release (or autorepeat) follows whatever the press did, even if bindings changed since.
                action = self.pressed.get(ev.code, self.bindings.get(src, {'type': 'default'}))
                if ev.value == 0:
                    self.pressed.pop(ev.code, None)
            if action['type'] != 'default':
                if ev.value == 1:
                    self._press(action)
                elif ev.value == 0:
                    self._release(action)
                return
        elif ev.type == e.EV_REL and ev.code in (e.REL_WHEEL, e.REL_WHEEL_HI_RES) and ev.value:
            action = self.bindings.get('WHEEL_UP' if ev.value > 0 else 'WHEEL_DOWN')
            if action:
                if ev.code == e.REL_WHEEL:  # one action per notch; the hi-res duplicates are dropped
                    for _ in range(abs(ev.value)):
                        self._press(action)
                        self._release(action)
                return
        elif ev.type == e.EV_SYN:
            self.pointer.syn()
            return
        elif ev.type == e.EV_MSC:
            return  # scan codes would describe the physical button, not the remapped one
        self.pointer.write(ev.type, ev.code, ev.value)


def _bindings_active(cfg: dict) -> dict:
    return {k: v for k, v in cfg['bindings'].items()
            if k in SOURCES and v.get('type', 'default') != 'default'}


def run() -> None:
    """Foreground loop: `hyperx-haste2 remap`. Exits when bindings are disabled in the config."""
    problem = check()
    if problem and 'not found' not in problem:  # an unplugged mouse is waited for, not fatal
        raise RemapError(problem)
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK, 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RemapError('the remapper is already running') from None

    remappers: dict[str, _Remapper] = {}
    mtime = None
    cfg = load()
    next_scan = 0.0
    try:
        while True:
            try:
                m = CONFIG.stat().st_mtime_ns
            except OSError:
                m = None
            if m != mtime:
                mtime, cfg = m, load()
                if not _bindings_active(cfg):
                    return
            now = time.monotonic()
            if now >= next_scan:
                next_scan = now + 2
                for path in _find_mice():
                    if path not in remappers:
                        try:
                            remappers[path] = _Remapper(path)
                        except OSError as ex:
                            print(f'cannot remap {path}: {ex}', file=sys.stderr)
            active = _bindings_active(cfg)
            for r in remappers.values():
                r.bindings = active
            ready = select.select([r.dev.fd for r in remappers.values()], [], [], 1.0)[0] if remappers \
                else time.sleep(1) or []
            for path, r in list(remappers.items()):
                if r.dev.fd not in ready:
                    continue
                try:
                    for ev in r.dev.read():
                        r.handle(ev)
                except OSError:  # unplugged
                    r.close()
                    del remappers[path]
    finally:
        for r in remappers.values():
            r.close()
        lock.close()
