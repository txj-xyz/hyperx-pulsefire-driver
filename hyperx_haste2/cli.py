"""Command line interface: `python -m hyperx_haste2 <command>`."""
from __future__ import annotations

import argparse
import sys

from . import desktop
from . import protocol as p


def cmd_probe(args) -> None:
    """Read-only: list HID nodes and dump the raw replies to every known query."""
    for n in p.list_nodes():
        print(f'{n.path}  iface {n.interface}  {n.mode}  descriptor starts {p.hexdump(n.descriptor, 6)}')
    with p.Device() as dev:
        print(f'\nconfig interface: {dev.node.path}')
        for rid, sub, name in [(0x10, 0x01, 'info'), (0x50, 0x02, 'battery'),
                               (0x32, 0x02, 'dpi'), (0x40, 0x02, 'brightness')]:
            try:
                for rep in dev.transact(p.packet(rid, sub), bytes([rid + 1, sub])):
                    print(f'{name:>10} {rid:02x} {sub:02x} -> {p.hexdump(rep)}')
            except p.DeviceError as e:
                print(f'{name:>10} {rid:02x} {sub:02x} -> {e}')


def cmd_status(args) -> None:
    with p.Device() as dev:
        b = dev.battery()
        t = dev.dpi_table()
        print(f'Device:     {dev.node.path} ({dev.node.mode})')
        print(f'Battery:    {b.percent}%  {b.state_text}  {b.voltage_mv} mV  {b.temperature_c} °C')
        print(f'Polling:    {t.polling_hz} Hz')
        for i, (s, on) in enumerate(zip(t.stages, t.enabled)):
            mark = '*' if i == t.active else ' '
            print(f'DPI {i + 1}: {mark} {s.dpi:>5}  #{bytes(s.color).hex()}  {"" if on else "(disabled)"}')
        print(f'Brightness: {round(dev.brightness() * 100 / 255)}%')
    try:
        a = desktop.read()
        print(f'Accel:      {a.profile}, speed {a.speed:+.2f} ({desktop.backend()} desktop setting)')
    except (desktop.DesktopError, OSError) as e:
        print(f'Accel:      unavailable ({e})')


def cmd_set(args) -> None:
    if args.accel or args.accel_speed is not None:
        try:
            cur = desktop.read()
            print('Accel:', desktop.apply(desktop.Accel(args.accel or cur.profile,
                                                        cur.speed if args.accel_speed is None else args.accel_speed)))
        except (desktop.DesktopError, ValueError) as e:
            sys.exit(f'error: {e}')
    if not any([args.dpi, args.stage, args.polling, args.color, args.brightness is not None]):
        return cmd_status(args)
    with p.Device() as dev:
        if args.dpi or args.stage or args.polling:
            t = dev.dpi_table()
            if args.dpi:
                for i, v in enumerate(args.dpi):
                    t.stages[i].dpi = v
            if args.stage:
                t.active = args.stage - 1
            if args.polling:
                t.polling_hz = args.polling
            dev.set_dpi_table(t, onboard=args.save)
        if args.color:
            dev.set_color(tuple(bytes.fromhex(args.color.lstrip('#'))))
        if args.brightness is not None:
            dev.set_brightness(round(args.brightness * 255 / 100))
    cmd_status(args)


def cmd_remap(args) -> None:
    from . import remap
    if not remap.load()['bindings']:
        sys.exit('error: no button bindings are set; set them in the GUI\'s Bindings tab')
    try:
        remap.run()
    except remap.RemapError as e:
        sys.exit(f'error: {e}')
    except KeyboardInterrupt:
        pass


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog='hyperx-haste2', description='HyperX Pulsefire Haste 2 Pro 4K control')
    sub = ap.add_subparsers(dest='cmd')
    sub.add_parser('probe', help='dump raw replies (read only)').set_defaults(fn=cmd_probe)
    sub.add_parser('status', help='show battery, DPI, polling').set_defaults(fn=cmd_status)
    s = sub.add_parser('set', help='change settings')
    s.add_argument('--dpi', type=int, nargs='+', metavar='N', help='DPI for stage 1, 2, ...')
    s.add_argument('--stage', type=int, choices=range(1, 6), help='active DPI stage')
    s.add_argument('--polling', type=int, choices=sorted(p.POLLING_TO_CODE), help='Hz')
    s.add_argument('--color', help='LED colour, e.g. ff0000')
    s.add_argument('--brightness', type=int, choices=range(0, 101), metavar='0-100')
    s.add_argument('--accel', choices=desktop.PROFILES, help='pointer acceleration profile (desktop setting)')
    s.add_argument('--accel-speed', type=float, metavar='-1..1', help='pointer speed (desktop setting), 0 = default')
    s.add_argument('--save', action='store_true', help='also write DPI/polling to onboard memory')
    s.set_defaults(fn=cmd_set)
    sub.add_parser('remap', help='run the button bindings set in the GUI (stays in the foreground)').set_defaults(
        fn=cmd_remap)
    sub.add_parser('gui', help='open the settings window').set_defaults(fn=lambda a: __import__(
        'hyperx_haste2.gui', fromlist=['main']).main())
    args = ap.parse_args(argv)
    try:
        (getattr(args, 'fn', None) or (lambda a: __import__('hyperx_haste2.gui', fromlist=['main']).main()))(args)
    except p.DeviceError as e:
        sys.exit(f'error: {e}')
