#!/usr/bin/env python3
"""Generate the distributed PNG icon sizes from the canonical SVG artwork.

The SVG files in ``icons/`` are the design source of truth.  QtSvg is used
instead of ImageMagick so rasterization matches the Qt runtime that displays
the application icon.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "icons"
APPIMAGE = ROOT / "packaging" / "appimage" / "hicolor"
PACKAGE = ROOT / "src" / "voxelmill" / "data" / "icons"
SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512, 1024)
VARIANTS = {
    "voxelmill": "voxelmill.svg",
    "voxelmill-light": "voxelmill-light.svg",
    "voxelmill-symbolic": "voxelmill-symbolic.svg",
}


def _render(svg: Path, png: Path, size: int) -> None:
    from PySide6.QtCore import QRectF, QSize
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(str(svg))
    if not renderer.isValid():
        raise SystemExit(f"invalid SVG: {svg}")
    image = QImage(QSize(size, size), QImage.Format.Format_RGBA8888)
    image.fill(0)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    png.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(png), "PNG"):
        raise SystemExit(f"could not write {png}")


def _package_assets() -> set[str]:
    """Names the Python package's icon directory is allowed to contain."""
    return {svg for svg in VARIANTS.values()} | {f"{name}.png" for name in VARIANTS}


def _expected_paths():
    for name, svg_name in VARIANTS.items():
        svg = SOURCE / svg_name
        yield name, svg, APPIMAGE / "scalable" / "apps" / svg_name
        for size in SIZES:
            yield name, svg, APPIMAGE / f"{size}x{size}" / "apps" / f"{name}.png"


def generate() -> None:
    # Qt must be initialized before QSvgRenderer is used in headless builds.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    del app
    for name, svg, destination in _expected_paths():
        if destination.suffix == ".svg":
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(svg, destination)
        else:
            _render(svg, destination, int(destination.parent.parent.name.split("x")[0]))

    # The desktop entry and AppImage root use the dark 256px fallback.
    shutil.copyfile(APPIMAGE / "256x256" / "apps" / "voxelmill.png",
                    ROOT / "packaging" / "appimage" / "voxelmill.png")

    # Runtime assets travel with the Python package: every SVG source variant
    # plus one 256px PNG each as a fallback for Qt builds without QtSvg.  The
    # symbolic variant is the one the editor uses for small/toolbar contexts.
    # Larger rasters stay out of the wheel; the hicolor tree already carries
    # them for desktop-environment lookup.
    PACKAGE.mkdir(parents=True, exist_ok=True)
    for stale in PACKAGE.iterdir():
        if stale.is_file() and stale.name not in _package_assets():
            stale.unlink()
    for name, svg_name in VARIANTS.items():
        shutil.copyfile(SOURCE / svg_name, PACKAGE / svg_name)
        shutil.copyfile(APPIMAGE / "256x256" / "apps" / f"{name}.png",
                        PACKAGE / f"{name}.png")


def check() -> int:
    failures = []
    for name, svg, destination in _expected_paths():
        if destination.suffix == ".svg":
            if not destination.is_file() or destination.read_bytes() != svg.read_bytes():
                failures.append(f"SVG differs: {destination}")
        elif not destination.is_file() or destination.stat().st_size == 0:
            failures.append(f"missing PNG: {destination}")
    for svg_name in VARIANTS.values():
        package_svg = PACKAGE / svg_name
        if not package_svg.is_file() or package_svg.read_bytes() != (SOURCE / svg_name).read_bytes():
            failures.append(f"SVG differs: {package_svg}")
    for name in VARIANTS:
        package_png = PACKAGE / f"{name}.png"
        if not package_png.is_file() or package_png.stat().st_size == 0:
            failures.append(f"missing PNG: {package_png}")
    allowed = _package_assets()
    for present in sorted(PACKAGE.iterdir()):
        if present.is_file() and present.name not in allowed:
            failures.append(f"unexpected package asset: {present}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check generated assets without rewriting them")
    args = parser.parse_args(argv)
    if args.check:
        return check()
    generate()
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
