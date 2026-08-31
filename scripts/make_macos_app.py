#!/usr/bin/env python3
"""Build the native 'Frankly Zapped.app' (SwiftUI) — macOS only, needs the Command Line
Tools (swift, sips, iconutil). No Xcode required.

Usage: python scripts/make_macos_app.py [--dest ~/Applications] [--icon file.png] [--tk]

Default builds ``macos/Zap`` in release mode and bundles the binary. ``--tk``
instead wraps the stdlib Tkinter ``zap-gui`` (portable fallback) in a compiled
exec() shim. The app locates the ``zap`` CLI via $ZAP_BIN, ~/.local/bin, or PATH.
"""

import argparse
import os
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def bolt_png(size: int = 512) -> bytes:
    """Render the same bolt as zap/gui.py into a PNG (pure stdlib)."""

    bg, disc, bolt = (0, 0, 0, 0), (52, 52, 60, 255), (255, 204, 0, 255)
    poly = [(0.58, 0.08), (0.30, 0.55), (0.48, 0.55), (0.40, 0.92), (0.72, 0.42), (0.54, 0.42)]
    pts = [(x * size, y * size) for x, y in poly]

    def inside(px, py):
        hit, j = False, len(pts) - 1
        for i, (xi, yi) in enumerate(pts):
            xj, yj = pts[j]
            if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi + 1e-9) + xi:
                hit = not hit
            j = i
        return hit

    c, r2 = size / 2, (size * 0.46) ** 2
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            if inside(x + 0.5, y + 0.5):
                raw += bytes(bolt)
            elif (x - c) ** 2 + (y - c) ** 2 <= r2:
                raw += bytes(disc)
            else:
                raw += bytes(bg)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def make_icns(png: Path, out: Path) -> None:
    with tempfile.TemporaryDirectory() as td:
        iconset = Path(td) / "zap.iconset"
        iconset.mkdir()
        for n in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = n * scale
                name = f"icon_{n}x{n}{'@2x' if scale == 2 else ''}.png"
                subprocess.run(["sips", "-z", str(px), str(px), str(png), "--out", str(iconset / name)],
                               check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=str(Path.home() / "Applications"))
    ap.add_argument("--icon", help="PNG to use instead of the built-in bolt")
    ap.add_argument("--tk", action="store_true", help="bundle the Tkinter zap-gui instead of the SwiftUI app")
    args = ap.parse_args()
    if sys.platform != "darwin":
        print("macOS only", file=sys.stderr)
        return 2

    app = Path(args.dest).expanduser() / "Frankly Zapped.app"
    if app.exists():
        shutil.rmtree(app)
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Resources").mkdir()
    launcher = app / "Contents" / "MacOS" / "zap"

    if args.tk:
        gui_bin = ROOT / ".venv" / "bin" / "zap-gui"
        if not gui_bin.exists():
            gui_bin = Path(shutil.which("zap-gui") or "")
        if not gui_bin.exists():
            print("zap-gui not found; run `pip install -e .` first", file=sys.stderr)
            return 1
        # Launch Services refuses to `open` a bundle whose executable is a shell
        # script (error -10669), so compile a 3-line exec() shim with clang.
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "launch.c"
            src.write_text('#include <unistd.h>\n#include <stdio.h>\n'
                           'int main(int c, char **v) { (void)c; execv(TARGET, v); perror("zap-gui"); return 1; }\n')
            subprocess.run(["clang", "-O2", f'-DTARGET="{gui_bin}"', "-o", str(launcher), str(src)], check=True)
    else:
        pkg = ROOT / "macos" / "Zap"
        subprocess.run(["swift", "build", "-c", "release", "--product", "Zap"], cwd=pkg, check=True)
        bin_path = subprocess.run(["swift", "build", "-c", "release", "--show-bin-path"], cwd=pkg,
                                  check=True, capture_output=True, text=True).stdout.strip()
        shutil.copy2(Path(bin_path) / "Zap", launcher)

    with tempfile.TemporaryDirectory() as td:
        png = Path(args.icon) if args.icon else Path(td) / "bolt.png"
        if not args.icon:
            png.write_bytes(bolt_png())
        make_icns(png, app / "Contents" / "Resources" / "zap.icns")

    version = "0.2.0"
    try:
        import importlib.metadata as md
        version = md.version("zap-uninstaller")
    except Exception:  # noqa: BLE001
        pass
    plistlib.dump({
        "CFBundleName": "Frankly Zapped", "CFBundleDisplayName": "Frankly Zapped",
        "CFBundleIdentifier": "dev.waltspence.frankly-zapped",
        "CFBundleVersion": version, "CFBundleShortVersionString": version,
        "CFBundleExecutable": "zap", "CFBundleIconFile": "zap",
        "CFBundlePackageType": "APPL", "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.utilities",
    }, (app / "Contents" / "Info.plist").open("wb"))
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True, capture_output=True)
    os.utime(app)  # nudge Finder to refresh the icon
    print(app)
    return 0


if __name__ == "__main__":
    sys.exit(main())
