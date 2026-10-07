"""HID protocol for the HyperX Pulsefire Haste 2 Pro 4K (03f0:0cbd / 03f0:0abd).

The mouse exposes four HID interfaces. Configuration happens on the vendor
interface (usage page 0xFF00) whose descriptor declares report IDs
0x10..0xFC as 64-byte output reports, with the device answering on the next
odd ID (0x10 -> 0x11, 0x32 -> 0x33, ...). 0xFF is a generic ACK and 0xFB an
asynchronous notification (e.g. DPI button presses).

This is the same command family used by other current HyperX mice
(Pulsefire Saga Pro, Haste 2 Core Wireless); see README for references.

Packet layouts, all zero-padded to 64 bytes:

  10 01                  -> 11 01 ...            device info
  50 02                  -> 51 02 PP SS TT 00 VV VV   battery %, state, temp C, mV
  32 02                  -> 33 02 RR MM AA [5 x (DPI_LE16, R, G, B)]
  32 01 PP 00 RR MM AA ..   write DPI/polling table (PP 0 = live, 1 = onboard profile)
  40 02                  -> 41 02 BB             LED brightness
  40 01 PP 00 BB            set brightness
  44 01 NN 00               begin direct colour frame of NN chunks
  44 02 II 00 R G B ...     colour chunk II
  ff 01 ... [14]=rid [15]=sub [16]=result   ACK (result 0 = OK)
  fb 08 SS CC               DPI stage changed by the button on the mouse

  RR (polling) = 8000 / Hz  ->  0x40 125, 0x20 250, 0x10 500, 0x08 1000, 0x04 2000, 0x02 4000
  DPI code     = DPI / 50 - 1
"""
from __future__ import annotations

import dataclasses
import os
import select
import time
from pathlib import Path

VID = 0x03F0
PIDS = {0x0CBD: 'wireless', 0x0ABD: 'wired'}
BUS_BT = 0x0005
REPORT_SIZE = 64

POLLING_TO_CODE = {125: 0x40, 250: 0x20, 500: 0x10, 1000: 0x08, 2000: 0x04, 4000: 0x02}
CODE_TO_POLLING = {v: k for k, v in POLLING_TO_CODE.items()}
DPI_MIN, DPI_MAX, DPI_STEP = 50, 26000, 50

BATTERY_STATES = {0: 'On battery', 1: 'Charging', 2: 'Fully charged'}


class DeviceError(RuntimeError):
    pass


class DeviceDisconnected(DeviceError):
    pass


@dataclasses.dataclass
class HidNode:
    path: str
    pid: int
    bus: int
    interface: int
    descriptor: bytes

    @property
    def mode(self) -> str:
        if self.bus == BUS_BT:
            return 'bluetooth'
        return PIDS[self.pid]


def _uevent(entry: Path) -> dict[str, str]:
    try:
        text = (entry / 'device' / 'uevent').read_text()
    except OSError:
        return {}
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def list_nodes() -> list[HidNode]:
    nodes = []
    for entry in sorted(Path('/sys/class/hidraw').glob('hidraw*')):
        props = _uevent(entry)
        parts = props.get('HID_ID', '').split(':')
        if len(parts) != 3 or int(parts[1], 16) != VID or int(parts[2], 16) not in PIDS:
            continue
        bus = int(parts[0], 16)
        phys = props.get('HID_PHYS', '')
        iface = int(phys.rsplit('input', 1)[1]) if 'input' in phys else -1
        try:
            desc = (entry / 'device' / 'report_descriptor').read_bytes()
        except OSError:
            desc = b''
        nodes.append(HidNode(f'/dev/{entry.name}', int(parts[2], 16), bus, iface, desc))
    return nodes


def find_bluetooth_node() -> HidNode | None:
    """Returns the first Bluetooth HID node for the mouse, if connected via BT."""
    return next((n for n in list_nodes() if n.mode == 'bluetooth'), None)


def find_config_node() -> HidNode | None:
    """The vendor interface (usage page 0xFF00..0xFFFF) that declares the 0x32 and 0x50 reports.
    Wired mode uses usage page 0xFF13; wireless uses 0xFF00.
    Wired is tried first: when the cable is plugged in, the mouse switches to wired mode
    and the wireless dongle's vendor interface stops responding."""
    mode_order = {'wired': 0, 'wireless': 1, 'bluetooth': 2}
    nodes = sorted(list_nodes(), key=lambda n: mode_order.get(n.mode, 9))
    for node in nodes:
        d = node.descriptor
        # Any vendor-defined usage page: 06 XX FF
        if len(d) < 3 or d[0] != 0x06 or d[2] != 0xFF:
            continue
        if b'\x85\x32' in d and b'\x85\x50' in d:
            return node
    return None


def packet(*data: int) -> bytes:
    if len(data) > REPORT_SIZE:
        raise ValueError('packet too long')
    return bytes(data).ljust(REPORT_SIZE, b'\0')


def hexdump(data: bytes, limit: int = REPORT_SIZE) -> str:
    return ' '.join(f'{b:02x}' for b in data[:limit])


@dataclasses.dataclass
class Battery:
    percent: int
    state: int
    temperature_c: int
    voltage_mv: int

    @property
    def state_text(self) -> str:
        return BATTERY_STATES.get(self.state, f'unknown (0x{self.state:02x})')


@dataclasses.dataclass
class DpiStage:
    dpi: int
    color: tuple[int, int, int]


@dataclasses.dataclass
class DpiTable:
    polling_hz: int
    enabled_mask: int
    active: int
    stages: list[DpiStage]
    raw: bytes  # the full 33 02 reply, so unknown trailing bytes survive a write

    @property
    def enabled(self) -> list[bool]:
        return [bool(self.enabled_mask & (1 << i)) for i in range(len(self.stages))]


def dpi_to_code(dpi: int) -> int:
    if not DPI_MIN <= dpi <= DPI_MAX or dpi % DPI_STEP:
        raise ValueError(f'DPI must be {DPI_MIN}..{DPI_MAX} in steps of {DPI_STEP}')
    return dpi // DPI_STEP - 1


def parse_dpi_table(rep: bytes) -> DpiTable:
    if rep[:2] != b'\x33\x02':
        raise DeviceError(f'unexpected DPI reply: {hexdump(rep, 16)}')
    rate = CODE_TO_POLLING.get(rep[2])
    if rate is None:
        raise DeviceError(f'unknown polling code 0x{rep[2]:02x}')
    stages = []
    for i in range(5):
        off = 5 + i * 5
        code = int.from_bytes(rep[off:off + 2], 'little')
        stages.append(DpiStage((code + 1) * DPI_STEP, tuple(rep[off + 2:off + 5])))
    return DpiTable(rate, rep[3], rep[4], stages, bytes(rep))


def build_dpi_write(table: DpiTable, onboard: bool = False) -> bytes:
    """Rebuild a 32 01 write from a table, preserving bytes we do not model."""
    if table.polling_hz not in POLLING_TO_CODE:
        raise ValueError(f'polling rate must be one of {sorted(POLLING_TO_CODE)}')
    if not 0 <= table.active < len(table.stages) or not table.enabled_mask & (1 << table.active):
        raise ValueError('active stage must be an enabled stage')
    body = bytearray(table.raw[2:62])
    body[0] = POLLING_TO_CODE[table.polling_hz]
    body[1] = table.enabled_mask
    body[2] = table.active
    for i, st in enumerate(table.stages):
        off = 3 + i * 5
        body[off:off + 2] = dpi_to_code(st.dpi).to_bytes(2, 'little')
        body[off + 2:off + 5] = bytes(st.color)
    return packet(0x32, 0x01, 1 if onboard else 0, 0x00, *body)


class Device:
    """One open handle on the config interface. Not thread safe; use from one thread."""

    def __init__(self, node: HidNode | None = None):
        node = node or find_config_node()
        if node is None:
            raise DeviceError('Pulsefire Haste 2 Pro not found (is the dongle/cable plugged in?)')
        self.node = node
        try:
            self.fd = os.open(node.path, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError as e:
            raise DeviceError(
                f'No permission to open {node.path}. Install the udev rule '
                '(see README) and replug the dongle.') from e
        self.dpi_stage_events: list[int] = []

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _read(self, timeout: float) -> bytes | None:
        if not select.select([self.fd], [], [], timeout)[0]:
            return None
        try:
            return os.read(self.fd, REPORT_SIZE)
        except BlockingIOError:
            return None

    def _note(self, rep: bytes) -> bool:
        """Swallow async notifications; returns True if rep was one."""
        if rep[:1] == b'\xfb':
            if rep[1] == 0x08:
                self.dpi_stage_events.append(rep[2])
            return True
        return False

    def drain(self) -> None:
        while (rep := self._read(0)) is not None:
            self._note(rep)

    def transact(self, data: bytes, reply: bytes | None = None, timeout: float = 0.6) -> list[bytes]:
        """Send one report. Waits for `reply` prefix if given, else for the ACK.

        Returns every non-notification report received (useful for probing)."""
        self.drain()
        if os.write(self.fd, data) != len(data):
            raise DeviceError('short HID write')
        got = []
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            rep = self._read(left)
            if rep is None:
                break
            if self._note(rep):
                continue
            got.append(rep)
            if reply is not None and rep.startswith(reply):
                return got
            if rep[:2] == b'\xff\x01' and rep[14] in (data[0], data[0] + 1) and rep[15] == data[1]:
                if rep[16] != 0:
                    raise DeviceError(f'mouse rejected {data[0]:02x} {data[1]:02x} (code {rep[16]})')
                if reply is None:
                    return got
        if reply is None and not got:
            raise DeviceError('no response; mouse may be asleep, wiggle it and retry')
        if reply is not None:
            raise DeviceError(f'no {hexdump(reply)} reply; mouse may be asleep, wiggle it and retry')
        return got

    def _query(self, rid: int, sub: int) -> bytes:
        prefix = bytes([rid + 1, sub])
        return next(r for r in self.transact(packet(rid, sub), prefix) if r.startswith(prefix))

    # --- reads -----------------------------------------------------------
    def info_raw(self) -> bytes:
        return self._query(0x10, 0x01)

    def battery(self) -> Battery:
        r = self._query(0x50, 0x02)
        return Battery(r[2], r[3], r[4], int.from_bytes(r[6:8], 'little'))

    def dpi_table(self) -> DpiTable:
        return parse_dpi_table(self._query(0x32, 0x02))

    def brightness(self) -> int:
        """0..255"""
        return self._query(0x40, 0x02)[2]

    # --- writes ----------------------------------------------------------
    def set_dpi_table(self, table: DpiTable, onboard: bool = False) -> DpiTable:
        self.transact(build_dpi_write(table, onboard))
        back = self.dpi_table()
        want = (table.polling_hz, table.enabled_mask, table.active, [s.dpi for s in table.stages])
        have = (back.polling_hz, back.enabled_mask, back.active, [s.dpi for s in back.stages])
        if want != have:
            raise DeviceError(f'readback mismatch: wanted {want}, mouse reports {have}')
        return back

    def set_color(self, rgb: tuple[int, int, int]) -> None:
        self.transact(packet(0x44, 0x01, 0x01, 0x00))
        self.transact(packet(0x44, 0x02, 0x00, 0x00, *rgb))

    def set_brightness(self, level: int) -> None:
        self.transact(packet(0x40, 0x01, 0x00, 0x00, max(0, min(255, level))))
