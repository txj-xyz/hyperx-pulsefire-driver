#!/bin/sh
# Bundle the project source next to the PKGBUILD so `makepkg -si` can build it locally.
set -e
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
ver=$(sed -n 's/^pkgver=//p' "$here/PKGBUILD")
name=hyperx-haste2-$ver
tar -czf "$here/$name.tar.gz" -C "$root" --transform "s,^,$name/," \
    pyproject.toml README.md LICENSE hyperx_haste2/__init__.py hyperx_haste2/__main__.py \
    hyperx_haste2/cli.py hyperx_haste2/desktop.py hyperx_haste2/gui.py hyperx_haste2/protocol.py hyperx_haste2/remap.py \
    udev packaging/hyperx-haste2.desktop packaging/hyperx-haste2.svg
echo "wrote $here/$name.tar.gz"
