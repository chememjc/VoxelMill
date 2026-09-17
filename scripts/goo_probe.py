#!/usr/bin/env python3
"""Standalone ELEGOO GOO v3.0 header parser + RLE layer decoder / verifier.

Usage: goo_probe.py [--all-layers] <file.goo>
       goo_probe.py --selftest

Prints every header field, then decodes the first 3 and last 3 layers,
reporting decoded pixel count, image dimensions, and whether the RLE
stream terminated exactly on the expected byte boundary with a matching
checksum.  ``--all-layers`` also performs the same bounded Python decode of
every RLE stream without materializing images. Exits non-zero if any checked
layer fails to decode cleanly.

No third-party dependencies (numpy is used only if available, for speed).
"""

import struct
import sys

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

HEADER_SIZE = 195477
LAYER_DEF_SIZE = 70
FOOTER_SIZE = 11
FILE_MAGIC = bytes([0x07, 0x00, 0x00, 0x00, 0x44, 0x4C, 0x50, 0x00])
DELIMITER = b"\x0d\x0a"
LAYER_MAGIC = 0x55
SMALL_PREVIEW_WH = (116, 116)
BIG_PREVIEW_WH = (290, 290)

# (offset, size, struct-format-or-special, name)
# 's'  -> NUL-terminated fixed-length string
# 'raw'-> keep bytes (previews / delimiters)
HEADER_FIELDS = [
    (0,      4,      's',   'Version'),
    (4,      8,      'raw', 'Magic'),
    (12,     32,     's',   'SoftwareName'),
    (44,     24,     's',   'SoftwareVersion'),
    (68,     24,     's',   'FileCreateTime'),
    (92,     32,     's',   'MachineName'),
    (124,    32,     's',   'MachineType'),
    (156,    32,     's',   'ProfileName'),
    (188,    2,      '>H',  'AntiAliasingLevel'),
    (190,    2,      '>H',  'GreyLevel'),
    (192,    2,      '>H',  'BlurLevel'),
    (194,    26912,  'raw', 'SmallPreview565'),
    (27106,  2,      'raw', 'SmallPreviewDelimiter'),
    (27108,  168200, 'raw', 'BigPreview565'),
    (195308, 2,      'raw', 'BigPreviewDelimiter'),
    (195310, 4,      '>I',  'LayerCount'),
    (195314, 2,      '>H',  'ResolutionX'),
    (195316, 2,      '>H',  'ResolutionY'),
    (195318, 1,      '>B',  'MirrorX'),
    (195319, 1,      '>B',  'MirrorY'),
    (195320, 4,      '>f',  'DisplayWidth'),
    (195324, 4,      '>f',  'DisplayHeight'),
    (195328, 4,      '>f',  'MachineZ'),
    (195332, 4,      '>f',  'LayerHeight'),
    (195336, 4,      '>f',  'ExposureTime'),
    (195340, 1,      '>B',  'DelayMode'),
    (195341, 4,      '>f',  'LightOffDelay'),
    (195345, 4,      '>f',  'BottomWaitTimeAfterCure'),
    (195349, 4,      '>f',  'BottomWaitTimeAfterLift'),
    (195353, 4,      '>f',  'BottomWaitTimeBeforeCure'),
    (195357, 4,      '>f',  'WaitTimeAfterCure'),
    (195361, 4,      '>f',  'WaitTimeAfterLift'),
    (195365, 4,      '>f',  'WaitTimeBeforeCure'),
    (195369, 4,      '>f',  'BottomExposureTime'),
    (195373, 4,      '>I',  'BottomLayerCount'),
    (195377, 4,      '>f',  'BottomLiftHeight'),
    (195381, 4,      '>f',  'BottomLiftSpeed'),
    (195385, 4,      '>f',  'LiftHeight'),
    (195389, 4,      '>f',  'LiftSpeed'),
    (195393, 4,      '>f',  'BottomRetractHeight'),
    (195397, 4,      '>f',  'BottomRetractSpeed'),
    (195401, 4,      '>f',  'RetractHeight'),
    (195405, 4,      '>f',  'RetractSpeed'),
    (195409, 4,      '>f',  'BottomLiftHeight2'),
    (195413, 4,      '>f',  'BottomLiftSpeed2'),
    (195417, 4,      '>f',  'LiftHeight2'),
    (195421, 4,      '>f',  'LiftSpeed2'),
    (195425, 4,      '>f',  'BottomRetractHeight2'),
    (195429, 4,      '>f',  'BottomRetractSpeed2'),
    (195433, 4,      '>f',  'RetractHeight2'),
    (195437, 4,      '>f',  'RetractSpeed2'),
    (195441, 2,      '>H',  'BottomLightPWM'),
    (195443, 2,      '>H',  'LightPWM'),
    (195445, 1,      '>B',  'PerLayerSettings'),
    (195446, 4,      '>I',  'PrintTime'),
    (195450, 4,      '>f',  'Volume'),
    (195454, 4,      '>f',  'MaterialGrams'),
    (195458, 4,      '>f',  'MaterialCost'),
    (195462, 8,      's',   'PriceCurrencySymbol'),
    (195470, 4,      '>I',  'LayerDefAddress'),
    (195474, 1,      '>B',  'GrayScaleLevel'),
    (195475, 2,      '>H',  'TransitionLayerCount'),
]

LAYER_FIELDS = [
    (0,  2, '>H', 'Pause'),
    (2,  4, '>f', 'PausePositionZ'),
    (6,  4, '>f', 'PositionZ'),
    (10, 4, '>f', 'ExposureTime'),
    (14, 4, '>f', 'LightOffDelay'),
    (18, 4, '>f', 'WaitTimeAfterCure'),
    (22, 4, '>f', 'WaitTimeAfterLift'),
    (26, 4, '>f', 'WaitTimeBeforeCure'),
    (30, 4, '>f', 'LiftHeight'),
    (34, 4, '>f', 'LiftSpeed'),
    (38, 4, '>f', 'LiftHeight2'),
    (42, 4, '>f', 'LiftSpeed2'),
    (46, 4, '>f', 'RetractHeight'),
    (50, 4, '>f', 'RetractSpeed'),
    (54, 4, '>f', 'RetractHeight2'),
    (58, 4, '>f', 'RetractSpeed2'),
    (62, 2, '>H', 'LightPWM'),
    (64, 2, 'raw', 'Delimiter'),
    (66, 4, '>I', 'DataLength'),
]


def cstr(b):
    return b.split(b"\x00", 1)[0].decode("ascii", "replace")


def parse_header(buf):
    out = {}
    for off, size, kind, name in HEADER_FIELDS:
        chunk = buf[off:off + size]
        if kind == 's':
            out[name] = cstr(chunk)
        elif kind == 'raw':
            out[name] = chunk
        else:
            out[name] = struct.unpack(kind, chunk)[0]
    return out


def parse_layer_def(buf):
    out = {}
    for off, size, kind, name in LAYER_FIELDS:
        chunk = buf[off:off + size]
        out[name] = chunk if kind == 'raw' else struct.unpack(kind, chunk)[0]
    return out


class RleError(Exception):
    pass


def decode_rle(rle, width, height, want_image=False):
    """Decode one layer's RLE blob (magic .. checksum inclusive).

    Returns dict with pixels decoded, checksum status, and how many bytes
    of the payload were consumed.  Raises RleError on structural problems.
    """
    npix = width * height
    if len(rle) < 3:
        raise RleError("RLE blob shorter than 3 bytes (%d)" % len(rle))
    if rle[0] != LAYER_MAGIC:
        raise RleError("bad layer magic 0x%02x (expected 0x55)" % rle[0])

    last = len(rle) - 1                    # index of the checksum byte
    checksum = (~sum(rle[1:last])) & 0xFF
    checksum_ok = (checksum == rle[last])

    img = bytearray(npix) if want_image else None
    pixel = 0
    color = 0
    i = 1
    chunks = 0
    while i < last:
        b0 = rle[i]
        ctype = b0 >> 6
        stride = 0
        idx0, idx1, idx2, idx3 = i, i + 1, i + 2, i + 3

        if ctype == 0:
            color = 0
        elif ctype == 1:
            if i + 1 >= last:
                raise RleError("truncated grayscale chunk at byte %d" % i)
            i += 1
            color = rle[i]
            idx1 += 1
            idx2 += 1
            idx3 += 1
        elif ctype == 2:
            dtype = (b0 >> 4) & 0x3
            dval = b0 & 0xF
            if dtype == 0:
                color = (color + dval) & 0xFF
                stride = 1
            elif dtype == 1:
                if i + 1 >= last:
                    raise RleError("truncated diff chunk at byte %d" % i)
                color = (color + dval) & 0xFF
                i += 1
                stride = rle[i]
            elif dtype == 2:
                color = (color - dval) & 0xFF
                stride = 1
            else:
                if i + 1 >= last:
                    raise RleError("truncated diff chunk at byte %d" % i)
                color = (color - dval) & 0xFF
                i += 1
                stride = rle[i]
        else:
            color = 0xFF

        if ctype != 2:
            nlen = (b0 >> 4) & 0x3
            lastidx = (idx0, idx1, idx2, idx3)[nlen]
            if lastidx >= last:
                raise RleError("truncated run length at byte %d" % i)
            if nlen == 0:
                stride = b0 & 0xF
            elif nlen == 1:
                stride = (rle[idx1] << 4) + (b0 & 0xF)
                i += 1
            elif nlen == 2:
                stride = (rle[idx1] << 12) + (rle[idx2] << 4) + (b0 & 0xF)
                i += 2
            else:
                stride = ((rle[idx1] << 20) + (rle[idx2] << 12) +
                          (rle[idx3] << 4) + (b0 & 0xF))
                i += 3

        if stride <= 0:
            raise RleError("zero/negative run length at byte %d" % i)
        if pixel + stride > npix:
            raise RleError("run overflows image at pixel %d (+%d > %d)"
                           % (pixel, stride, npix))
        if img is not None and color:
            img[pixel:pixel + stride] = bytes([color]) * stride
        pixel += stride
        chunks += 1
        i += 1

    return {
        'pixels': pixel,
        'expected_pixels': npix,
        'chunks': chunks,
        'consumed': i,
        'payload_end': last,
        'ended_on_boundary': i == last,
        'checksum_ok': checksum_ok,
        'checksum_stored': rle[last],
        'checksum_computed': checksum,
        'image': (np.frombuffer(bytes(img), dtype=np.uint8).reshape(height, width)
                  if (img is not None and np is not None) else img),
    }


def encode_rle(pixels, use_diff=True):
    """Encode a row-major bytes-like grayscale buffer into a GOO RLE blob.

    Mirrors the reference encoder: magic 0x55, chunks, 8-bit checksum.
    Provided so an implementer can round-trip against decode_rle().
    """
    out = bytearray()
    out.append(LAYER_MAGIC)
    state = {'prev': 0, 'cur': 0, 'stride': 0}

    def add_rep():
        stride = state['stride']
        if stride == 0:
            return
        cur, prev = state['cur'], state['prev']
        fb = len(out)
        out.append(0)
        cd = abs(cur - prev)
        if use_diff and cd <= 0xF and stride <= 0xFF and 0 < cur < 0xFF:
            out[fb] = (0b10 << 6) | (cd & 0xF)
            if stride > 1:
                out[fb] |= 0x1 << 4
                out.append(stride & 0xFF)
            if cur < prev:
                out[fb] |= 0x1 << 5
            return
        if cur == 0xFF:
            out[fb] |= 0b11 << 6
        elif cur > 0:
            out[fb] |= 0b01 << 6
            out.append(cur)
        out[fb] |= stride & 0xF
        if stride <= 0xF:
            return
        if stride <= 0xFFF:
            out[fb] |= 0b01 << 4
            out.append((stride >> 4) & 0xFF)
            return
        if stride <= 0xFFFFF:
            out[fb] |= 0b10 << 4
            out.append((stride >> 12) & 0xFF)
            out.append((stride >> 4) & 0xFF)
            return
        if stride <= 0xFFFFFFF:
            out[fb] |= 0b11 << 4
            out.append((stride >> 20) & 0xFF)
            out.append((stride >> 12) & 0xFF)
            out.append((stride >> 4) & 0xFF)
            return
        raise RleError("run of %d pixels is too large to encode" % stride)

    for v in pixels:
        if v == state['cur']:
            state['stride'] += 1
        else:
            add_rep()
            state['stride'] = 1
            state['prev'] = state['cur']
            state['cur'] = v
    add_rep()
    cs = 0
    for b in out[1:]:
        cs = (cs + b) & 0xFF
    out.append((~cs) & 0xFF)
    return bytes(out)


def selftest():
    """Exercise every chunk type and every run-length form."""
    import random
    random.seed(7)
    w = hgt = 64
    px = bytearray(w * hgt)
    px[0:20] = bytes(20)
    px[20:40] = b"\xff" * 20
    px[w:2 * w] = b"\xaa" * w                      # type 01 plateau
    px[2 * w:3 * w] = b"\x01" * w
    px[3 * w:4 * w] = b"\xfe" * w
    for i in range(w):                              # small deltas -> type 10
        px[4 * w + i] = 100 + (i % 16)
    for i in range(5 * w, 10 * w):
        px[i] = random.randint(1, 254)
    for i in range(10 * w, w * hgt):
        px[i] = random.choice([0, 255, 0x7F, 0x80])
    px = bytes(px)
    rc = 0
    for use_diff in (True, False):
        blob = encode_rle(px, use_diff)
        r = decode_rle(blob, w, hgt, want_image=True)
        img = r['image']
        img = img.tobytes() if np is not None else bytes(img)
        ok = (img == px and r['pixels'] == w * hgt
              and r['ended_on_boundary'] and r['checksum_ok'])
        print("selftest diff_mode=%-5s: %d bytes, %d/%d px, boundary=%s, "
              "checksum=%s, round-trip=%s"
              % (use_diff, len(blob), r['pixels'], w * hgt,
                 r['ended_on_boundary'], r['checksum_ok'], img == px))
        if not ok:
            rc = 1
    # 28-bit run-length form
    blob = encode_rle(bytes(40000000))
    r = decode_rle(blob, 40000000, 1)
    print("selftest 40,000,000-px zero run: chunk=%s -> %d px, checksum=%s"
          % (blob[1:-1].hex(' '), r['pixels'], r['checksum_ok']))
    if r['pixels'] != 40000000 or not r['checksum_ok']:
        rc = 1
    return rc


def main(argv):
    if len(argv) == 2 and argv[1] == '--selftest':
        return selftest()
    all_layers = len(argv) == 3 and argv[1] == '--all-layers'
    if len(argv) != 2 and not all_layers:
        print(__doc__)
        return 2
    path = argv[2] if all_layers else argv[1]
    failures = []

    with open(path, 'rb') as f:
        f.seek(0, 2)
        filesize = f.tell()
        f.seek(0)
        hdr_buf = f.read(HEADER_SIZE)
        if len(hdr_buf) != HEADER_SIZE:
            print("ERROR: file shorter than a GOO header")
            return 1
        h = parse_header(hdr_buf)

        print("=== FILE ===")
        print("path                     : %s" % path)
        print("size                     : %d bytes" % filesize)
        print()
        print("=== HEADER (offset 0, %d bytes) ===" % HEADER_SIZE)
        for off, size, kind, name in HEADER_FIELDS:
            v = h[name]
            if kind == 'raw':
                if size > 32:
                    v = "<%d bytes> first8=%s" % (size, v[:8].hex(' '))
                else:
                    v = v.hex(' ')
            elif kind == '>f':
                v = "%g" % v
            print("  +%-7d %-6d %-6s %-26s = %s" % (off, size, kind, name, v))
        print()

        if h['Version'] != 'V3.0':
            failures.append("Version is %r, not 'V3.0'" % h['Version'])
        if h['Magic'] != FILE_MAGIC:
            failures.append("header magic mismatch: %s" % h['Magic'].hex(' '))
        if h['SmallPreviewDelimiter'] != DELIMITER:
            failures.append("small preview delimiter mismatch")
        if h['BigPreviewDelimiter'] != DELIMITER:
            failures.append("big preview delimiter mismatch")
        if h['LayerDefAddress'] != HEADER_SIZE:
            failures.append("LayerDefAddress %d != header size %d"
                            % (h['LayerDefAddress'], HEADER_SIZE))

        print("=== PREVIEWS ===")
        print("  small preview : %dx%d RGB565-BE, %d bytes at +194"
              % (SMALL_PREVIEW_WH + (26912,)))
        print("  big preview   : %dx%d RGB565-BE, %d bytes at +27108"
              % (BIG_PREVIEW_WH + (168200,)))
        print()

        # ---- footer ----
        f.seek(filesize - FOOTER_SIZE)
        footer = f.read(FOOTER_SIZE)
        print("=== FOOTER (last %d bytes) ===" % FOOTER_SIZE)
        print("  padding : %s" % footer[:3].hex(' '))
        print("  magic   : %s  (%s)"
              % (footer[3:].hex(' '),
                 "OK" if footer[3:] == FILE_MAGIC else "MISMATCH"))
        if footer[3:] != FILE_MAGIC:
            failures.append("footer magic mismatch")
        print()

        # ---- walk the layer chain ----
        w, hgt = h['ResolutionX'], h['ResolutionY']
        n = h['LayerCount']
        print("=== LAYER CHAIN ===")
        print("  layer count       : %d" % n)
        print("  image dimensions  : %d x %d  (%d pixels)" % (w, hgt, w * hgt))
        print("  first layer at    : +%d" % h['LayerDefAddress'])

        offsets = []
        off = h['LayerDefAddress']
        bad_delims = 0
        for _ in range(n):
            offsets.append(off)
            f.seek(off + 64)
            tail = f.read(6)
            dl = struct.unpack('>I', tail[2:6])[0]
            if tail[0:2] != DELIMITER:
                bad_delims += 1
            f.seek(off + LAYER_DEF_SIZE + dl)
            if f.read(2) != DELIMITER:
                bad_delims += 1
            off += LAYER_DEF_SIZE + dl + 2
        end_of_layers = off
        print("  end of last layer : +%d" % end_of_layers)
        print("  bytes after that  : %d (footer is %d)"
              % (filesize - end_of_layers, FOOTER_SIZE))
        print("  delimiters checked: %d (2 per layer), bad: %d"
              % (2 * n, bad_delims))
        if bad_delims:
            failures.append("%d bad 0x0d 0x0a delimiters in the layer chain"
                            % bad_delims)
        if filesize - end_of_layers != FOOTER_SIZE:
            failures.append("layer chain does not end exactly %d bytes "
                            "before EOF (got %d)"
                            % (FOOTER_SIZE, filesize - end_of_layers))
        print()

        if all_layers:
            decoded = 0
            for li, off in enumerate(offsets):
                f.seek(off)
                ld = parse_layer_def(f.read(LAYER_DEF_SIZE))
                rle = f.read(ld['DataLength'])
                try:
                    r = decode_rle(rle, w, hgt, want_image=False)
                    if (r['pixels'] != r['expected_pixels'] or not r['ended_on_boundary']
                            or not r['checksum_ok']):
                        raise RleError('pixel count, boundary, or checksum did not validate')
                except RleError as error:
                    failures.append('layer %d: %s' % (li, error))
                    break
                decoded += 1
            print('  all-layer RLE decode : %d/%d passed (no images materialized)' % (decoded, n))
            if decoded != n:
                failures.append('only %d of %d layer RLE streams decoded' % (decoded, n))
            print()

        probe = list(range(min(3, n))) + [i for i in range(max(0, n - 3), n)
                                          if i >= 3]
        for li in probe:
            off = offsets[li]
            f.seek(off)
            ld = parse_layer_def(f.read(LAYER_DEF_SIZE))
            rle = f.read(ld['DataLength'])
            trail = f.read(2)
            print("--- layer %d @ +%d ---" % (li, off))
            for foff, fsize, kind, name in LAYER_FIELDS:
                v = ld[name]
                if kind == 'raw':
                    v = v.hex(' ')
                elif kind == '>f':
                    v = "%g" % v
                print("    +%-3d %-4s %-18s = %s" % (foff, kind, name, v))
            ok = True
            if ld['Delimiter'] != DELIMITER:
                failures.append("layer %d: bad in-record delimiter %s"
                                % (li, ld['Delimiter'].hex()))
                ok = False
            if trail != DELIMITER:
                failures.append("layer %d: bad trailing delimiter %s"
                                % (li, trail.hex()))
                ok = False
            try:
                r = decode_rle(rle, w, hgt, want_image=True)
            except RleError as e:
                print("    DECODE FAILED: %s" % e)
                failures.append("layer %d: %s" % (li, e))
                print()
                continue
            print("    rle bytes          : %d (magic + %d payload + checksum)"
                  % (ld['DataLength'], ld['DataLength'] - 2))
            print("    chunks             : %d" % r['chunks'])
            print("    decoded pixels     : %d / %d  (%dx%d)"
                  % (r['pixels'], r['expected_pixels'], w, hgt))
            print("    ended on boundary  : %s (stopped at payload byte %d of %d)"
                  % (r['ended_on_boundary'], r['consumed'], r['payload_end']))
            print("    checksum           : stored 0x%02x, computed 0x%02x -> %s"
                  % (r['checksum_stored'], r['checksum_computed'],
                     "MATCH" if r['checksum_ok'] else "MISMATCH"))
            if np is not None and r['image'] is not None:
                img = r['image']
                nz = int((img > 0).sum())
                print("    non-zero pixels    : %d (%.4f%%), max gray %d"
                      % (nz, 100.0 * nz / (w * hgt), int(img.max())))
            if r['pixels'] != r['expected_pixels']:
                failures.append("layer %d: decoded %d pixels, expected %d"
                                % (li, r['pixels'], r['expected_pixels']))
                ok = False
            if not r['ended_on_boundary']:
                failures.append("layer %d: RLE did not end on the checksum "
                                "byte boundary" % li)
                ok = False
            if not r['checksum_ok']:
                failures.append("layer %d: checksum mismatch" % li)
                ok = False
            print("    RESULT             : %s" % ("PASS" if ok else "FAIL"))
            print()

    print("=== SUMMARY ===")
    if failures:
        for msg in failures:
            print("  FAIL: %s" % msg)
        print("  %d problem(s) found." % len(failures))
        return 1
    print("  All checks passed.")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
