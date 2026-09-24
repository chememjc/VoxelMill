"""Printer slice formats, one registry entry each.

Everything that depends on which printer file format is in hand goes through
:func:`for_path`: reading headers and layers for ``info`` and the editor's
layer view, verifying a finished file, and choosing reader/writer pairs for
``convert``. Adding a format means adding one :class:`SliceFormat` subclass to
:data:`FORMATS`; callers do not test suffixes themselves.

Readers and verifiers are imported lazily so importing this module stays cheap.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .contracts import VoxelMillError, no_progress


class SliceFormat:
    """What callers need from one printer file format."""

    name = ''       # short identifier used in reports: 'goo', 'ctb'
    suffix = ''     # lowercase file suffix including the dot
    label = ''      # human-readable name for status lines and help
    has_previews = False

    def reader(self, path):
        """Open ``path``; the result is a context manager with ``header``, ``layers``,
        ``shape`` and ``decode(index)``."""
        raise NotImplementedError

    def writer_class(self):
        raise NotImplementedError

    def layer_z(self, layer):
        """Z of one entry of ``reader.layers``, in mm."""
        raise NotImplementedError

    def display_frame(self, reader, index, cancel=None):
        """``(grid, frame)``: a decoded layer unmirrored into plate orientation.

        Diagnostics carry plate coordinates, so a frame drawn beside them has
        to be unmirrored first or they land on opposite sides.
        """
        raise NotImplementedError

    def verify(self, path, settings, *, budget=None, cancel=None, progress=no_progress,
               track_voids=True, full_panel=False):
        raise NotImplementedError

    # Shared behaviour built on the hooks above --------------------------------

    def summary(self, path):
        """Header, layer table and previews, for the editor's file source."""
        with self.reader(path) as reader:
            return {
                'path': str(path),
                'format': self.name,
                'header': dict(reader.header),
                'layer_count': len(reader.layers),
                'shape_px': list(reader.shape),
                'z_mm': [float(self.layer_z(layer)) for layer in reader.layers],
                'small_preview': (np.array(reader.small_preview, copy=True)
                                  if self.has_previews else None),
                'big_preview': (np.array(reader.big_preview, copy=True)
                                if self.has_previews else None),
            }

    def info(self, path, *, decode_all=False):
        """What ``voxelmill info`` prints: header and layer count, optionally
        after decoding every layer to prove its framing."""
        with self.reader(path) as reader:
            payload = {'format': self.name, 'header': reader.header, 'layers': len(reader.layers)}
            if decode_all:
                for index in range(len(reader.layers)):
                    reader.decode(index)
                payload['decoded_layers'] = len(reader.layers)
        return payload

    def display_layer(self, path, index, *, cancel=None):
        """One decoded, unmirrored layer in the payload ``LayerView`` expects."""
        with self.reader(path) as reader:
            count = len(reader.layers)
            if index < 0 or index >= count:
                raise VoxelMillError('invalid_layer', f'Layer {index} is outside a {count} layer file',
                                     {'index': int(index), 'layer_count': count})
            grid, frame = self.display_frame(reader, index, cancel=cancel)
            frame = np.ascontiguousarray(frame)
            return {'grid': grid, 'index': int(index),
                    'z_mm': float(self.layer_z(reader.layers[index])),
                    'mask': frame, 'open_rows': 0,
                    'filled_pixels': int(np.count_nonzero(frame)),
                    # The Layers tab's file source is named 'goo' for every
                    # format; ``format`` tells them apart in the status line.
                    'source': 'goo', 'format': self.name, 'source_path': str(path)}


class GooFormat(SliceFormat):
    name, suffix, label = 'goo', '.goo', 'GOO'
    has_previews = True

    def reader(self, path):
        from .goo import GooReader
        return GooReader(path)

    def writer_class(self):
        from .goo import GooWriter
        return GooWriter

    def layer_z(self, layer):
        return layer.values['position_z']

    def display_frame(self, reader, index, cancel=None):
        from .goo import GooLayerStream
        stream = GooLayerStream(reader, cancel=cancel)
        frame = reader.decode(index)
        if stream.mirror_x:
            frame = frame[:, ::-1]
        if stream.mirror_y:
            frame = frame[::-1, :]
        return stream.grid, frame

    def verify(self, path, settings, *, budget=None, cancel=None, progress=no_progress,
               track_voids=True, full_panel=False):
        from .goo import verify_goo
        return verify_goo(path, settings, budget=budget, cancel=cancel, progress=progress,
                          track_voids=track_voids, full_panel=full_panel)


class CtbFormat(SliceFormat):
    """Classic, unencrypted CTB v3. Encrypted and v4/v5 files are refused by the reader."""
    name, suffix, label = 'ctb', '.ctb', 'CTB'

    def reader(self, path):
        from .ctb import CtbReader
        return CtbReader(path)

    def writer_class(self):
        from .ctb import CtbWriter
        return CtbWriter

    def layer_z(self, layer):
        return layer.z_mm

    def display_frame(self, reader, index, cancel=None):
        from .raster import RasterGrid
        header = reader.header
        width, height = header['resolution_x'], header['resolution_y']
        grid = RasterGrid(width, height, -header['display_width'] / 2, -header['display_height'] / 2,
                          header['display_width'] / width, header['display_height'] / height)
        frame = reader.decode(index)
        # CTB records only an X flip (projector_type); it has no Y mirror.
        if header['projector_type']:
            frame = frame[:, ::-1]
        return grid, frame

    def verify(self, path, settings, *, budget=None, cancel=None, progress=no_progress,
               track_voids=True, full_panel=False):
        # verify_ctb always analyzes the decoded frames as they are; there is
        # no cropped mode to widen, so full_panel changes nothing here.
        from .ctb import verify_ctb
        return verify_ctb(path, settings, budget=budget, cancel=cancel, progress=progress,
                          track_voids=track_voids)


#: Every supported format, keyed by suffix.
FORMATS = {fmt.suffix: fmt for fmt in (GooFormat(), CtbFormat())}


def for_path(path):
    """The format that owns ``path``'s suffix, or a ``slice_format`` error."""
    suffix = Path(path).suffix.lower()
    try:
        return FORMATS[suffix]
    except KeyError:
        known = ', '.join(sorted(FORMATS))
        raise VoxelMillError('slice_format', f'Unsupported printer file {suffix or "(no suffix)"}; '
                             f'expected one of {known}', {'path': str(path)}) from None
