# ELEGOO GOO v3.0 slice file — binary format

Everything below is written so that an encoder and a decoder can be implemented
from this document alone. Every offset, size, type and endianness claim was
checked byte-for-byte against a real file (see
[Verification against the reference file](#verification-against-the-reference-file)).

## Sources

| # | Source | Consulted | Revision pinned |
|---|--------|-----------|-----------------|
| 1 | ELEGOO official format description — <https://github.com/elegooofficial/GOO> , document `Goo Format Spec V1.2.pdf` (<https://raw.githubusercontent.com/elegooofficial/GOO/main/Goo%20Format%20Spec%20V1.2.pdf>) | 2026-09-06 | branch `main`; PDF is 116 764 bytes |
| 2 | UVtools independent implementation — <https://raw.githubusercontent.com/sn4k3/UVtools/master/UVtools.Core/FileFormats/GooFile.cs> | 2026-09-06 | repo `master` HEAD `3e3c62faef56a4ae77e2c652cf4914472ec7aa66` (2026-09-05); the file itself was last changed in `be94ce79a62d4b30efa015e0b3db26c08646ac97` ("v6.2.0", 2026-07-26) |
| 3 | Reference file `right_temporal_bone_mars5_oriented_1_202609061627_04h20m_77ml_uvtools-good.goo`, 190 971 963 bytes, ELEGOO Mars 5 Ultra | 2026-09-06 | n/a |

The official specification is the normative description of V3.0. UVtools and
the immutable reference are interoperability evidence: they resolve an obvious
documentation typo or show what a current slicer emits, but do not establish
unverified firmware behavior. See
[Where the official document and UVtools disagree](#where-the-official-document-and-uvtools-disagree).

## File layout at a glance

```
+0            FileHeader              195 477 bytes, fixed size
+195477       LayerDef[0]              70 bytes
              RLE blob[0]              DataLength[0] bytes
              0x0D 0x0A                 2 bytes
              LayerDef[1]              70 bytes
              RLE blob[1]              DataLength[1] bytes
              0x0D 0x0A                 2 bytes
              ... LayerCount times ...
EOF-11        FileFooter               11 bytes
```

There is no layer index/offset table. Layers are **chained**: you must read a
layer's `DataLength` to find the next one. The header's `LayerDefAddress`
points at the first `LayerDef`; in every file seen it equals the fixed header
size, 195 477.

## Endianness — verified, not assumed

GOO is **big-endian for every multi-byte numeric field**, including the
IEEE-754 `float32`s and the RGB565 preview pixels. Raw bytes from the reference
file, decoded both ways:

| Field | Raw bytes | Big-endian | Little-endian |
|---|---|---|---|
| `LayerCount` (u32) | `00 00 07 28` | **1832** | 671 547 392 |
| `ResolutionX` (u16) | `21 48` | **8520** | 18 465 |
| `ResolutionY` (u16) | `10 e0` | **4320** | 57 360 |
| `MachineZ` (f32) | `43 25 00 00` | **165.0** | 1.34e-41 |
| `DisplayWidth` (f32) | `43 19 5c 29` | **153.36** | 4.89e-14 |
| `PrintTime` (u32) | `00 00 3d 14` | **15636** (= 4 h 20 m, matches the filename `04h20m`) | 339 542 016 |
| `LayerDefAddress` (u32) | `00 02 fb 95` | **195477** (= header size) | 2 516 255 232 |

Only the big-endian reading yields sane values in every case, so the claim
holds. The preview pixels were checked separately: decoding the RGB565 words
big-endian gives a near-neutral gray render (mean per-channel deviation 7.8/255,
consistent with the gray model thumbnail); little-endian gives 86/255 of
channel noise. Preview words are therefore big-endian too.

Note the one place where "endianness" is inverted by design: RLE **run lengths**
are assembled from bytes in a big-endian-ish order but the *least* significant
nibble lives in byte 0. See [RLE](#layer-image-encoding-rle).

Byte-order-free fields: the 1-byte fields (`bool`, `DelayMode`,
`GrayScaleLevel`), the fixed-length ASCII strings, and the raw preview/
delimiter/magic byte arrays.

## Magic and delimiter bytes

| Name | Bytes | Where |
|---|---|---|
| Version string | `"V3.0"` = `56 33 2E 30` | file offset 0, 4 bytes |
| File magic | `07 00 00 00 44 4C 50 00` (i.e. `\x07\x00\x00\x00` + `"DLP\0"`) | file offset 4 (header), and again at `EOF-8` (footer) |
| Delimiter | `0D 0A` (CR LF) | after the small preview (+27106), after the big preview (+195308), inside every `LayerDef` at layer-relative +64, and after every layer's RLE blob |
| Layer RLE magic | `0x55` | first byte of every layer's RLE blob |

The delimiter is a fixed constant, not a terminator to be searched for — never
scan for `0D 0A`, always use the fixed offsets / lengths. (RLE payloads
routinely contain `0D 0A` internally.)

## Header

Fixed size **195 477 bytes**, offset 0. No padding or alignment anywhere; every
field is byte-packed. Strings are fixed-length, NUL-padded ASCII (a string
shorter than its field is followed by `0x00` to the end of the field).

`OBSERVED` is the value in the reference file.

| Offset | Size | Type (endian) | Field | Meaning | Observed |
|---:|---:|---|---|---|---|
| 0 | 4 | char[4] | `Version` | Format version string, `"V3.0"` | `V3.0` |
| 4 | 8 | byte[8] | `Magic` | File magic `07 00 00 00 44 4C 50 00` | `07 00 00 00 44 4c 50 00` |
| 12 | 32 | char[32] | `SoftwareName` | Slicer that produced the file | `ELEGOO SatelLite` |
| 44 | 24 | char[24] | `SoftwareVersion` | Slicer version | `1.0.2.29` |
| 68 | 24 | char[24] | `FileCreateTime` | Creation time, `"yyyy-MM-dd HH:mm:ss"` | `2026-09-06 16:27:32` |
| 92 | 32 | char[32] | `MachineName` | Printer name | `ELEGOO Mars 5 Ultra` |
| 124 | 32 | char[32] | `MachineType` | Printer type (official doc: "Printer type") | `ELEGOO Mars 5 Ultra` |
| 156 | 32 | char[32] | `ProfileName` | Resin/print profile name | `Jayo ABS-like Gray` |
| 188 | 2 | uint16 BE | `AntiAliasingLevel` | AA level chosen in the slicer | `0` |
| 190 | 2 | uint16 BE | `GreyLevel` | Grey level setting | `0` |
| 192 | 2 | uint16 BE | `BlurLevel` | Blur setting | `0` |
| 194 | 26912 | RGB565 BE[116×116] | `SmallPreview565` | Small preview bitmap, 2 bytes/pixel, row-major | 3 604 non-black pixels |
| 27106 | 2 | byte[2] | `SmallPreviewDelimiter` | `0D 0A` | `0d 0a` |
| 27108 | 168200 | RGB565 BE[290×290] | `BigPreview565` | Big preview bitmap, 2 bytes/pixel, row-major | 22 580 non-black pixels |
| 195308 | 2 | byte[2] | `BigPreviewDelimiter` | `0D 0A` | `0d 0a` |
| 195310 | 4 | uint32 BE | `LayerCount` | Number of layer records | `1832` |
| 195314 | 2 | uint16 BE | `ResolutionX` | LCD width in pixels | `8520` |
| 195316 | 2 | uint16 BE | `ResolutionY` | LCD height in pixels | `4320` |
| 195318 | 1 | bool/uint8 | `MirrorX` | 1 = image is X-mirrored by the slicer | `0` |
| 195319 | 1 | bool/uint8 | `MirrorY` | 1 = image is Y-mirrored by the slicer | `1` |
| 195320 | 4 | float32 BE | `DisplayWidth` | Active print area X, mm | `153.36` |
| 195324 | 4 | float32 BE | `DisplayHeight` | Active print area Y, mm | `77.76` |
| 195328 | 4 | float32 BE | `MachineZ` | Max Z travel, mm | `165` |
| 195332 | 4 | float32 BE | `LayerHeight` | Layer thickness, mm | `0.05` |
| 195336 | 4 | float32 BE | `ExposureTime` | Normal-layer exposure, s | `3.25` |
| 195340 | 1 | uint8 enum | `DelayMode` | 0 = light-off-delay mode, 1 = wait-time mode | `1` |
| 195341 | 4 | float32 BE | `LightOffDelay` | Light-off delay, s (used when `DelayMode`=0) | `0` |
| 195345 | 4 | float32 BE | `BottomWaitTimeAfterCure` | Bottom: wait after cure / before lift, s | `0.5` |
| 195349 | 4 | float32 BE | `BottomWaitTimeAfterLift` | Bottom: wait after lift, s | `0` |
| 195353 | 4 | float32 BE | `BottomWaitTimeBeforeCure` | Bottom: wait after retract / before cure, s | `0.5` |
| 195357 | 4 | float32 BE | `WaitTimeAfterCure` | Normal: wait after cure / before lift, s | `0.5` |
| 195361 | 4 | float32 BE | `WaitTimeAfterLift` | Normal: wait after lift, s | `0` |
| 195365 | 4 | float32 BE | `WaitTimeBeforeCure` | Normal: wait after retract / before cure, s | `0.5` |
| 195369 | 4 | float32 BE | `BottomExposureTime` | Bottom-layer exposure, s | `35` |
| 195373 | 4 | uint32 BE | `BottomLayerCount` | Number of bottom layers | `5` |
| 195377 | 4 | float32 BE | `BottomLiftHeight` | Bottom lift distance, mm | `0.05` |
| 195381 | 4 | float32 BE | `BottomLiftSpeed` | Bottom lift speed, mm/min | `0.05` |
| 195385 | 4 | float32 BE | `LiftHeight` | Normal lift distance, mm | `0.05` |
| 195389 | 4 | float32 BE | `LiftSpeed` | Normal lift speed, mm/min | `0.05` |
| 195393 | 4 | float32 BE | `BottomRetractHeight` | Bottom retract distance, mm | `0.05` |
| 195397 | 4 | float32 BE | `BottomRetractSpeed` | Bottom retract speed, mm/min | `0.05` |
| 195401 | 4 | float32 BE | `RetractHeight` | Normal retract distance, mm | `0.05` |
| 195405 | 4 | float32 BE | `RetractSpeed` | Normal retract speed, mm/min | `0.05` |
| 195409 | 4 | float32 BE | `BottomLiftHeight2` | Bottom 2nd-stage lift distance, mm | `0` |
| 195413 | 4 | float32 BE | `BottomLiftSpeed2` | Bottom 2nd-stage lift speed, mm/min | `0` |
| 195417 | 4 | float32 BE | `LiftHeight2` | Normal 2nd-stage lift distance, mm | `0` |
| 195421 | 4 | float32 BE | `LiftSpeed2` | Normal 2nd-stage lift speed, mm/min | `0` |
| 195425 | 4 | float32 BE | `BottomRetractHeight2` | Bottom 2nd-stage retract distance, mm | `0` |
| 195429 | 4 | float32 BE | `BottomRetractSpeed2` | Bottom 2nd-stage retract speed, mm/min | `0` |
| 195433 | 4 | float32 BE | `RetractHeight2` | Normal 2nd-stage retract distance, mm | `0` |
| 195437 | 4 | float32 BE | `RetractSpeed2` | Normal 2nd-stage retract speed, mm/min | `0` |
| 195441 | 2 | uint16 BE | `BottomLightPWM` | Bottom LED power, 0–255 | `255` |
| 195443 | 2 | uint16 BE | `LightPWM` | Normal LED power, 0–255 | `255` |
| 195445 | 1 | bool/uint8 | `PerLayerSettings` | 0 = use header values; 1 = "advance mode", use each `LayerDef`'s own values | `0` |
| 195446 | 4 | uint32 BE | `PrintTime` | Estimated print time, s | `15636` |
| 195450 | 4 | float32 BE | `Volume` | Total resin volume, mm³ | `76760.1` |
| 195454 | 4 | float32 BE | `MaterialGrams` | Total resin weight, g | `76.759` |
| 195458 | 4 | float32 BE | `MaterialCost` | Total resin cost | `1.057` |
| 195462 | 8 | char[8] | `PriceCurrencySymbol` | Currency symbol for `MaterialCost` | `$` |
| 195470 | 4 | uint32 BE | `LayerDefAddress` | Absolute file offset of the first `LayerDef` | `195477` |
| 195474 | 1 | bool/uint8 | `GrayScaleLevel` | 0 = pixel grays range 0x0–0xF; 1 = 0x00–0xFF | `0` |
| 195475 | 2 | uint16 BE | `TransitionLayerCount` | Number of exposure-transition layers after the bottom layers | `4` |

Total: 195 475 + 2 = **195 477 bytes**, which is exactly the observed
`LayerDefAddress`. There is no field 62 and no trailing header padding.

`GrayScaleLevel` semantics are worth a warning: the reference file stores `0`
("0x0–0xF") while its layers actually contain 8-bit grays `0x00` and `0xFF`.
Treat this field as an informational slicer hint, not as something that changes
how the RLE is decoded — the RLE always carries full 8-bit values. See
[disagreements](#where-the-official-document-and-uvtools-disagree).

## Layer records

Each layer is `70 + DataLength + 2` bytes:

```
LayerDef (70 bytes, fixed)  |  RLE blob (DataLength bytes)  |  0x0D 0x0A
```

Chaining: `offset[0] = header.LayerDefAddress`,
`offset[i+1] = offset[i] + 70 + DataLength[i] + 2`.
After the last layer comes the 11-byte footer, and nothing else — this is a
strong integrity check (see verification output below: `bytes after that: 11`).

Offsets are relative to the start of the `LayerDef`.

| Offset | Size | Type | Field | Meaning | Observed (layer 0 / layer 1831) |
|---:|---:|---|---|---|---|
| 0 | 2 | uint16 BE | `Pause` | 0 = reserved/none; 1 = pause printing at this layer | `0` / `0` |
| 2 | 4 | float32 BE | `PausePositionZ` | Z to lift to when `Pause`=1, mm | `165` / `165` |
| 6 | 4 | float32 BE | `PositionZ` | Absolute Z of this layer, mm | `0.05` / `91.6` |
| 10 | 4 | float32 BE | `ExposureTime` | Exposure for this layer, s | `35` / `3.25` |
| 14 | 4 | float32 BE | `LightOffDelay` | Light-off time (used when `DelayMode`=0), s | `0` / `0` |
| 18 | 4 | float32 BE | `WaitTimeAfterCure` | Wait after cure / before lift, s | `0.5` / `0.5` |
| 22 | 4 | float32 BE | `WaitTimeAfterLift` | Wait after lift, s | `0` / `0` |
| 26 | 4 | float32 BE | `WaitTimeBeforeCure` | Wait after retract / before cure, s | `0.5` / `0.5` |
| 30 | 4 | float32 BE | `LiftHeight` | Lift distance, mm | `0.05` / `0.05` |
| 34 | 4 | float32 BE | `LiftSpeed` | Lift speed, mm/min | `0.05` / `0.05` |
| 38 | 4 | float32 BE | `LiftHeight2` | 2nd-stage lift distance, mm | `0` / `0` |
| 42 | 4 | float32 BE | `LiftSpeed2` | 2nd-stage lift speed, mm/min | `0` / `0` |
| 46 | 4 | float32 BE | `RetractHeight` | Retract distance, mm | `0.05` / `0.05` |
| 50 | 4 | float32 BE | `RetractSpeed` | Retract speed, mm/min | `0.05` / `0.05` |
| 54 | 4 | float32 BE | `RetractHeight2` | 2nd-stage retract distance, mm | `0` / `0` |
| 58 | 4 | float32 BE | `RetractSpeed2` | 2nd-stage retract speed, mm/min | `0` / `0` |
| 62 | 2 | uint16 BE | `LightPWM` | LED power for this layer, 0–255 | `255` / `255` |
| 64 | 2 | byte[2] | `Delimiter` | Fixed `0D 0A` | `0d 0a` |
| 66 | 4 | uint32 BE | `DataLength` | Size of the RLE blob that follows, **including** the `0x55` magic byte and the trailing checksum byte | `376392` / `23` |
| 70 | `DataLength` | bytes | RLE blob | See below | — |
| 70+`DataLength` | 2 | byte[2] | trailing delimiter | Fixed `0D 0A` | `0d 0a` |

All 1832 layers of the reference file were checked: 3664/3664 delimiters
correct, and the chain lands exactly 11 bytes before EOF.

Per-layer values genuinely vary even though `PerLayerSettings` is `0`: layers
0–4 carry the bottom exposure 35 s, layers 5–8 carry the four transition
exposures 28.65 / 22.30 / 15.95 / 9.60 s, and layers 9+ carry 3.25 s. An encoder
should always write correct per-layer values regardless of that flag.

VoxelMill writes `PerLayerSettings = 1` because it deliberately supplies
per-layer bottom and transition exposure records. This follows the official
"advance mode" definition. The supplied Satellite reference uses `0` while
also varying those records, so firmware acceptance of the `1` path still needs
an explicit printer calibration test before it is called hardware-verified.

## Layer image encoding (RLE)

### Blob framing

```
byte 0                 : 0x55                      magic
bytes 1 .. N-2         : chunk stream (payload)
byte N-1               : checksum
```
where `N = DataLength`. The checksum is
`(~(sum of bytes 1..N-2)) & 0xFF` — an 8-bit wrapping sum of the payload,
bitwise-inverted. **The magic byte 0x55 is not included in the sum, and neither
is the checksum byte itself.** The trailing `0D 0A` after the blob is outside
`DataLength` and outside the checksum.

The chunk stream must decode to exactly `ResolutionX × ResolutionY` pixels and
must consume exactly the payload — the byte after the last chunk is the
checksum. Pixels are laid down row-major, left to right, top to bottom; runs
wrap freely across row boundaries (a single run may span the whole image).

Pixel values are 8-bit grayscale: `0x00` = LCD off, `0xFF` = full on,
intermediate values are anti-aliasing/grayscale exposure.

### Chunk layout

Byte 0 of every chunk:

```
 bit  7 6 | 5 4 | 3 2 1 0
      TYPE  LEN   low nibble of the run length (or the diff value)
```

`byte0[7:6]` — chunk type:

| Value | Meaning |
|---|---|
| `00` | Run of pixels with value `0x00` |
| `01` | Run of pixels with a gray value in `0x01`–`0xFE`; **the gray value is the byte immediately after byte 0** |
| `10` | Run whose value is the previous pixel value ± a 0–15 delta (see below) |
| `11` | Run of pixels with value `0xFF` |

`byte0[5:4]` — run-length form, for types `00`, `01`, `11` only:

| Value | Extra length bytes | Run length |
|---|---|---|
| `00` | none | `byte0[3:0]` — 4 bits, 0–15 |
| `01` | 1 (`byte1`) | `(byte1 << 4) \| byte0[3:0]` — 12 bits |
| `10` | 2 (`byte1`,`byte2`) | `(byte1 << 12) \| (byte2 << 4) \| byte0[3:0]` — 20 bits |
| `11` | 3 (`byte1`,`byte2`,`byte3`) | `(byte1 << 20) \| (byte2 << 12) \| (byte3 << 4) \| byte0[3:0]` — 28 bits, max 268 435 455 |

Note the ordering: the extra bytes are most-significant-first, and byte 0's low
nibble supplies the *least* significant 4 bits. For type `01`, the length bytes
come **after** the gray-value byte, i.e. the chunk is
`byte0, gray, [len bytes...]`.

`byte0[5:4]` when the type is `10` (difference chunk). `d = byte0[3:0]`, range
0–15; `prev` is the current running color:

| `byte0[5]` `byte0[4]` | Meaning |
|---|---|
| `0 0` | color = `prev + d`, run length 1 (no extra bytes) |
| `0 1` | color = `prev + d`, run length = next byte (`byte1`, 1–255) |
| `1 0` | color = `prev - d`, run length 1 (no extra bytes) |
| `1 1` | color = `prev - d`, run length = next byte (`byte1`, 1–255) |

Difference chunks never use the 12/20/28-bit length forms. The running color
carries across chunks of every type: after a type `00` chunk the running color
is `0x00`, after `11` it is `0xFF`, after `01` it is the gray byte, and after a
difference chunk it is the newly computed value. Arithmetic is 8-bit and wraps
(`(prev ± d) & 0xFF`), matching the reference implementation.

Worked examples (from the official document, corrected — see disagreements):

| Bytes | Decodes to |
|---|---|
| `3F 55 56 57` | type `00`, 28-bit form: value `0x00`, run `0x555657F` |
| `75 AA BB CC 15` | type `01`, 28-bit form: value `0xAA`, run `0xBBCC155` |
| `05` | type `00`, 4-bit form: value `0x00`, run `5` |
| `F1 CC BB AA` | type `11`, 28-bit form: value `0xFF`, run `0xCCBBAA1` |
| `81` | type `10`, `00`: color `prev+1`, run 1 |
| `92 FF` | type `10`, `01`: color `prev+2`, run `0xFF` |
| `A1` | type `10`, `10`: color `prev-1`, run 1 |
| `B2 EE` | type `10`, `11`: color `prev-2`, run `0xEE` |
| `30 26 25 A0` | type `00`, 28-bit form: value `0x00`, run 40 000 000 (produced and re-read by the probe's self-test) |

### Decode pseudocode

```
decode(rle: bytes, width, height) -> image[width*height]:
    assert len(rle) >= 3
    assert rle[0] == 0x55                       # layer magic
    last = len(rle) - 1                         # index of the checksum byte
    assert ((~sum(rle[1:last])) & 0xFF) == rle[last]

    img   = zeroed byte array of width*height
    pixel = 0                                   # write cursor
    color = 0                                   # running color
    i     = 1                                   # read cursor

    while i < last:
        b0     = rle[i]
        ctype  = b0 >> 6
        stride = 0
        idx0, idx1, idx2, idx3 = i, i+1, i+2, i+3

        if ctype == 0b00:
            color = 0x00
        elif ctype == 0b01:
            i += 1
            assert i < last
            color = rle[i]                      # gray byte
            idx1 += 1; idx2 += 1; idx3 += 1     # length bytes shift by one
        elif ctype == 0b10:
            dtype = (b0 >> 4) & 0b11
            d     = b0 & 0x0F
            color = (color + d) & 0xFF if dtype in (0b00, 0b01) else (color - d) & 0xFF
            if dtype in (0b01, 0b11):               # run length in the next byte
                i += 1
                assert i < last
                stride = rle[i]
            else:
                stride = 1
        else:                                   # 0b11
            color = 0xFF

        if ctype != 0b10:                       # run-length forms
            nlen = (b0 >> 4) & 0b11
            assert [idx0, idx1, idx2, idx3][nlen] < last
            if nlen == 0:
                stride = b0 & 0x0F
            elif nlen == 1:
                stride = (rle[idx1] << 4) | (b0 & 0x0F)
                i += 1
            elif nlen == 2:
                stride = (rle[idx1] << 12) | (rle[idx2] << 4) | (b0 & 0x0F)
                i += 2
            else:
                stride = (rle[idx1] << 20) | (rle[idx2] << 12) | (rle[idx3] << 4) | (b0 & 0x0F)
                i += 3

        assert stride > 0
        assert pixel + stride <= width*height
        fill img[pixel : pixel+stride] with color
        pixel += stride
        i += 1

    assert i == last                            # ended exactly on the checksum
    assert pixel == width*height
    return img
```

### Encode

A conforming encoder walks the image accumulating runs of equal pixel value and
emits one chunk per run. The reference (UVtools) encoder picks, for a run of
`stride` pixels of color `cur` following color `prev`:

1. **difference chunk** if `abs(cur-prev) <= 15` and `stride <= 255` and
   `0 < cur < 0xFF` — `byte0 = 0b10<<6 | (delta & 0xF)`, set bit 4 and append
   the stride byte when `stride > 1`, set bit 5 when `cur < prev`;
2. otherwise a type `00` / `01` / `11` chunk with the smallest run-length form
   that fits (`<=0xF` → 4-bit, `<=0xFFF` → 12-bit, `<=0xFFFFF` → 20-bit,
   `<=0xFFFFFFF` → 28-bit; larger runs cannot be encoded and must be split).

Then append `(~(sum of payload bytes)) & 0xFF`, and set
`DataLength = 1 + payload + 1`.

This algorithm was re-implemented in Python and reproduces the reference file's
RLE blobs **byte-for-byte** for layers 0, 1, 2, 900, 1829, 1830 and 1831 (see
verification). UVtools disables the difference chunk for `.prz` (Phrozen) files
but uses it for `.goo`.

## Previews

Two uncompressed bitmaps live inside the header. Both are **RGB565,
big-endian** (`RRRRRGGG GGGBBBBB`, high byte first), row-major, top-to-bottom,
no header, no padding, no stride alignment. Dimensions are **fixed** — an
encoder must scale/letterbox its thumbnail to exactly these sizes:

| Preview | Dimensions | Bytes | Header offset | Followed by |
|---|---|---:|---:|---|
| Small | 116 × 116 | 26 912 | +194 | `0D 0A` at +27106 |
| Big | 290 × 290 | 168 200 | +27108 | `0D 0A` at +195308 |

Channel extraction from a 16-bit word `v`: `R = (v >> 11) & 0x1F`,
`G = (v >> 5) & 0x3F`, `B = v & 0x1F`. Black (`0x0000`) is used for the
background. The reference file's previews are populated (3 604 and 22 580
non-black pixels, 32 and 38 distinct colors) and are near-neutral grays, which
is what confirmed the big-endian word order.

## Footer / ending magic

The last **11 bytes** of the file:

| Offset from EOF | Size | Field | Observed |
|---|---:|---|---|
| −11 | 3 | padding (purpose unconfirmed) | `00 00 00` |
| −8 | 8 | ending magic, identical to the header magic `07 00 00 00 44 4C 50 00` | `07 00 00 00 44 4c 50 00` |

A decoder should validate the ending magic; a file whose layer chain does not
end exactly 11 bytes before EOF is malformed.

## VoxelMill export and decoder contract

`slice_stl(source, output, settings, ...)` accepts a prepared, unioned STL. It
first checks the same usable envelope/edge clearance as `prepare`, then runs
raster and drainage validation on that exact STL before streaming one cropped
source layer at a time into a full LCD frame. The source hash is rechecked for
the write and post-write verification passes; a changed source aborts the
candidate. Source/output aliases and symlink output paths are rejected. The configured `image_mirror_x` and
`image_mirror_y` transformations are applied to the pixels and the same flags
are stored in the header. The staging file is reopened only after writing: each
layer is decoded and compared pixel-for-pixel against a fresh rasterization,
and header, transition exposure, waits, and motion fields are checked against
the resolved settings. Only then is the candidate atomically renamed to the
requested output path. A failed validation may be retained with
`allow_unresolved=True`, but this never bypasses envelope, framing, decoding,
or exact-pixel verification.

The writer emits full 8-bit `uint8` LCD frames. Its current slicer produces
binary `0`/`255` masks; grayscale RLE is codec-tested but antialiasing is not
enabled until occupancy and physical exposure have been calibrated. Previews
are deterministic gray height maps at the required 116×116 and 290×290 RGB565
sizes.

The reader is intentionally bounded. It rejects non-positive dimensions,
header images over 100,000,000 pixels, individual blobs over 512 MiB,
non-finite required numeric fields, missing delimiters at *every* layer, a
chain that does not terminate at the footer, malformed RLE, checksum errors,
and RLE whose decoded count does not exactly equal the image dimensions. The
Mars 5 Ultra reference is 8,520×4,320 (36,806,400 pixels), within that bound.

The default Mars 5 Ultra motion values were copied from the immutable reference
as a serializable starting point. They are neither measured machine settings
nor evidence for the units or tilt-release behavior. `PrintTime` defaults to
zero unless a caller supplies a measured/manual estimate: per-layer exposure
and wait fields are valid process settings, whereas a reliable total also
requires firmware-calibrated motion timing. Review all motion values manually
before a physical print.

## Where the official document and UVtools disagree

| Topic | ELEGOO spec v1.2 | UVtools `GooFile.cs` | Reference file supports |
|---|---|---|---|
| Header field 24 name/meaning | "Common exposure time" | `ExposureTime` | Same field; agree. |
| Header fields 27–29 / 30–32 naming | "Bottom before lift time / after lift time / after retract time"; "Before lift time / After lift time / After retract time" | `BottomWaitTimeAfterCure` / `AfterLift` / `BeforeCure` and `WaitTimeAfterCure` / `AfterLift` / `BeforeCure` | **Both, they are the same three slots in the same order** — "before lift" == "after cure" and "after retract" == "before cure". Values `0.5 / 0 / 0.5` are consistent either way. Naming only. |
| `MachineType` (field 6) content | "Printer type" | Written as the literal string `"DLP"` | The file contains `ELEGOO Mars 5 Ultra`, i.e. the printer name, not `"DLP"`. **Treat it as a free-form string; do not validate it against `"DLP"`.** (`"DLP"` does appear in the file — but as part of the 8-byte magic at +4, which is where the ELEGOO PDF's garbled table also puts it.) |
| Light PWM range | "0 ~ 255", stored as `short int` (2 bytes) | `ushort`, 2 bytes | 2-byte big-endian field holding `255`. Agree on layout; the useful range is 0–255 even though the field is 16 bits. |
| `GrayScaleLevel` (field 60) | `bool`: 0 → grays 0x0–0xF, 1 → grays 0x00–0xFF | Same field, but UVtools *defaults* it to `1` and never uses it when decoding | Reference file stores **`0`** yet contains `0xFF` pixels, so the field does **not** control RLE decoding. UVtools' behavior (ignore it) is what works. |
| `AntiAliasingLevel` | "Anti-aliasing level setting by slicer" | Defaults to `8` | Reference stores `0` (with `GreyLevel` 0 and `BlurLevel` 0) while producing purely 1-bit black/white layers. Informational only; do not derive decode behavior from it. |
| RLE example "run-length is 0xccbbaaff1" | Lists 4 bytes (`0xcc 0xbb 0xaa 0xff`) after a `11110001` byte 0 and claims a 36-bit length | Implements 3 length bytes → `0xCCBBAA1` | **UVtools.** The spec's own `byte0[5:4]` table caps the run at 28 bits; that example is a typo in the PDF. Its other examples (`0x555657F`, `0xBBCC155`) match UVtools exactly. |
| Layer-record field numbering | Layer-definition indices start at 1, and "Data size" and "Image data" are listed as *separate child fields* after the delimiter | One `LayerDef` struct: … `LightPWM`, `Delimiter[2]`, `DataLength` (u32), then the blob | Identical bytes on disk: `…, LightPWM@+62, 0D 0A@+64, DataLength@+66, blob@+70`. Presentation difference only. |
| Ending string | Mentioned ("header-info, layer-content and ending-string") but the layout is not given in the extractable text of the PDF | 3 padding bytes + the 8-byte file magic = 11 bytes | UVtools: the file ends `00 00 00 07 00 00 00 44 4C 50 00` and the layer chain stops exactly 11 bytes before EOF. |
| Header size | Not stated | Implied 195 477 (a code comment says so) | 195 477 — equals the observed `LayerDefAddress` and the first `LayerDef` starts there. |
| File version scope | Document covers V3.0 | `GooFile` accepts only version 30 (`V3.0`) and explicitly rejects `V5*`, which a separate `GooV5File` handles | Reference is `V3.0`. **A V5.x `.goo` is a different layout and is out of scope for this document.** |

## Fields that could not be confirmed

These are documented from the two written sources but are **unconfirmed**
against the reference file, because the file does not exercise them:

- **Chunk type `01` (gray `0x01`–`0xFE`) and chunk type `10` (difference)** —
  the reference file's layers use only types `00` and `11` (806 755 and 806 727
  chunks across a 28-layer sample; zero of the other two). Their layout comes
  from the spec + UVtools and was validated only by a synthetic round trip
  (`goo_probe.py --selftest`), which exercises all four types and all four
  length forms.
- **`Pause` / `PausePositionZ`** — `Pause` is `0` on every layer; the
  pause-at-Z behavior is unconfirmed.
- **`PerLayerSettings`** — `0` in the reference file, so the "advance mode"
  behavior (firmware honouring per-layer values) is unconfirmed.
- **`AntiAliasingLevel`, `GreyLevel`, `BlurLevel`** — all `0`; their encodings
  and effect on the printer are unconfirmed.
- **`GrayScaleLevel`** — stored `0` while the data is 8-bit; the intended
  meaning is unconfirmed (it is safe to ignore when decoding).
- **`MirrorX` = 0 / `MirrorY` = 1** — confirmed as 1-byte flags at those
  offsets, but whether the pixel data is already mirrored (vs. the printer
  mirroring it) is unconfirmed. The two reference GOO files for this printer
  disagree on these flags: this SatelLite file stores `mirror_x=0,
  mirror_y=1`, while a CHITUBOX Basic v2.3.1 reference for the same machine
  stores `mirror_x=1, mirror_y=0`. Which, if either, matches the physical LCD
  orientation is an open question — `scripts/goo_orientation_check.py` was run
  against both references from their shared source STL and came back
  inconclusive, because each slicer posed the model itself and the check
  cannot recover an unknown rotation about Z. See
  [troubleshooting.md](troubleshooting.md#goo_orientation_unverified) and
  `voxelmill.config`'s `printer.image_mirror_verified`, which every GOO export
  ships a warning against until this is resolved against a printed part.
- **`DelayMode` = 1** — the `0` (light-off-delay) path and the per-layer
  `LightOffDelay` semantics are unconfirmed.
- **Footer `Padding1..3`** — observed `00 00 00`; purpose unconfirmed.
- **`MachineType`** — observed to hold the printer name; whether firmware
  parses it at all is unconfirmed.
- **`PriceCurrencySymbol` encoding** for non-ASCII currency symbols is
  unconfirmed (only `"$"` observed).
- **Run lengths above 28 bits** cannot be encoded; whether firmware would
  tolerate a run split across two chunks is unconfirmed (though it is the only
  possible encoding, and UVtools' encoder never produces runs longer than the
  image).

## Verification against the reference file

Decoder/verifier: `/home3/noisecancelingcodex/scripts/goo_probe.py` (no
third-party dependencies; uses numpy only when present, for statistics). It
parses every header field, walks the whole layer chain checking all 3664
delimiters and the chain-to-footer arithmetic, and fully decodes the first three
and last three layers. It exits non-zero if anything fails — confirmed by
feeding it a copy of the last layer with one checksum byte flipped, which
reported `FAIL: layer 0: checksum mismatch` and exit status 1.

Command:

```
$ .venv/bin/python scripts/goo_probe.py \
    right_temporal_bone_mars5_oriented_1_202609061627_04h20m_77ml_uvtools-good.goo
```

Real output (exit status 0):

```
=== FILE ===
path                     : right_temporal_bone_mars5_oriented_1_202609061627_04h20m_77ml_uvtools-good.goo
size                     : 190971963 bytes

=== HEADER (offset 0, 195477 bytes) ===
  +0       4      s      Version                    = V3.0
  +4       8      raw    Magic                      = 07 00 00 00 44 4c 50 00
  +12      32     s      SoftwareName               = ELEGOO SatelLite
  +44      24     s      SoftwareVersion            = 1.0.2.29
  +68      24     s      FileCreateTime             = 2026-09-06 16:27:32
  +92      32     s      MachineName                = ELEGOO Mars 5 Ultra
  +124     32     s      MachineType                = ELEGOO Mars 5 Ultra
  +156     32     s      ProfileName                = Jayo ABS-like Gray
  +188     2      >H     AntiAliasingLevel          = 0
  +190     2      >H     GreyLevel                  = 0
  +192     2      >H     BlurLevel                  = 0
  +194     26912  raw    SmallPreview565            = <26912 bytes> first8=00 00 00 00 00 00 00 00
  +27106   2      raw    SmallPreviewDelimiter      = 0d 0a
  +27108   168200 raw    BigPreview565              = <168200 bytes> first8=00 00 00 00 00 00 00 00
  +195308  2      raw    BigPreviewDelimiter        = 0d 0a
  +195310  4      >I     LayerCount                 = 1832
  +195314  2      >H     ResolutionX                = 8520
  +195316  2      >H     ResolutionY                = 4320
  +195318  1      >B     MirrorX                    = 0
  +195319  1      >B     MirrorY                    = 1
  +195320  4      >f     DisplayWidth               = 153.36
  +195324  4      >f     DisplayHeight              = 77.76
  +195328  4      >f     MachineZ                   = 165
  +195332  4      >f     LayerHeight                = 0.05
  +195336  4      >f     ExposureTime               = 3.25
  +195340  1      >B     DelayMode                  = 1
  +195341  4      >f     LightOffDelay              = 0
  +195345  4      >f     BottomWaitTimeAfterCure    = 0.5
  +195349  4      >f     BottomWaitTimeAfterLift    = 0
  +195353  4      >f     BottomWaitTimeBeforeCure   = 0.5
  +195357  4      >f     WaitTimeAfterCure          = 0.5
  +195361  4      >f     WaitTimeAfterLift          = 0
  +195365  4      >f     WaitTimeBeforeCure         = 0.5
  +195369  4      >f     BottomExposureTime         = 35
  +195373  4      >I     BottomLayerCount           = 5
  +195377  4      >f     BottomLiftHeight           = 0.05
  +195381  4      >f     BottomLiftSpeed            = 0.05
  +195385  4      >f     LiftHeight                 = 0.05
  +195389  4      >f     LiftSpeed                  = 0.05
  +195393  4      >f     BottomRetractHeight        = 0.05
  +195397  4      >f     BottomRetractSpeed         = 0.05
  +195401  4      >f     RetractHeight              = 0.05
  +195405  4      >f     RetractSpeed               = 0.05
  +195409  4      >f     BottomLiftHeight2          = 0
  +195413  4      >f     BottomLiftSpeed2           = 0
  +195417  4      >f     LiftHeight2                = 0
  +195421  4      >f     LiftSpeed2                 = 0
  +195425  4      >f     BottomRetractHeight2       = 0
  +195429  4      >f     BottomRetractSpeed2        = 0
  +195433  4      >f     RetractHeight2             = 0
  +195437  4      >f     RetractSpeed2              = 0
  +195441  2      >H     BottomLightPWM             = 255
  +195443  2      >H     LightPWM                   = 255
  +195445  1      >B     PerLayerSettings           = 0
  +195446  4      >I     PrintTime                  = 15636
  +195450  4      >f     Volume                     = 76760.1
  +195454  4      >f     MaterialGrams              = 76.759
  +195458  4      >f     MaterialCost               = 1.057
  +195462  8      s      PriceCurrencySymbol        = $
  +195470  4      >I     LayerDefAddress            = 195477
  +195474  1      >B     GrayScaleLevel             = 0
  +195475  2      >H     TransitionLayerCount       = 4

=== PREVIEWS ===
  small preview : 116x116 RGB565-BE, 26912 bytes at +194
  big preview   : 290x290 RGB565-BE, 168200 bytes at +27108

=== FOOTER (last 11 bytes) ===
  padding : 00 00 00
  magic   : 07 00 00 00 44 4c 50 00  (OK)

=== LAYER CHAIN ===
  layer count       : 1832
  image dimensions  : 8520 x 4320  (36806400 pixels)
  first layer at    : +195477
  end of last layer : +190971952
  bytes after that  : 11 (footer is 11)
  delimiters checked: 3664 (2 per layer), bad: 0

--- layer 0 @ +195477 ---
    +0   >H   Pause              = 0
    +2   >f   PausePositionZ     = 165
    +6   >f   PositionZ          = 0.05
    +10  >f   ExposureTime       = 35
    +14  >f   LightOffDelay      = 0
    +18  >f   WaitTimeAfterCure  = 0.5
    +22  >f   WaitTimeAfterLift  = 0
    +26  >f   WaitTimeBeforeCure = 0.5
    +30  >f   LiftHeight         = 0.05
    +34  >f   LiftSpeed          = 0.05
    +38  >f   LiftHeight2        = 0
    +42  >f   LiftSpeed2         = 0
    +46  >f   RetractHeight      = 0.05
    +50  >f   RetractSpeed       = 0.05
    +54  >f   RetractHeight2     = 0
    +58  >f   RetractSpeed2      = 0
    +62  >H   LightPWM           = 255
    +64  raw  Delimiter          = 0d 0a
    +66  >I   DataLength         = 376392
    rle bytes          : 376392 (magic + 376390 payload + checksum)
    chunks             : 189497
    decoded pixels     : 36806400 / 36806400  (8520x4320)
    ended on boundary  : True (stopped at payload byte 376391 of 376391)
    checksum           : stored 0x37, computed 0x37 -> MATCH
    non-zero pixels    : 16707764 (45.3936%), max gray 255
    RESULT             : PASS

--- layer 1 @ +571941 ---
    +0   >H   Pause              = 0
    +2   >f   PausePositionZ     = 165
    +6   >f   PositionZ          = 0.1
    +10  >f   ExposureTime       = 35
    +14  >f   LightOffDelay      = 0
    +18  >f   WaitTimeAfterCure  = 0.5
    +22  >f   WaitTimeAfterLift  = 0
    +26  >f   WaitTimeBeforeCure = 0.5
    +30  >f   LiftHeight         = 0.05
    +34  >f   LiftSpeed          = 0.05
    +38  >f   LiftHeight2        = 0
    +42  >f   LiftSpeed2         = 0
    +46  >f   RetractHeight      = 0.05
    +50  >f   RetractSpeed       = 0.05
    +54  >f   RetractHeight2     = 0
    +58  >f   RetractSpeed2      = 0
    +62  >H   LightPWM           = 255
    +64  raw  Delimiter          = 0d 0a
    +66  >I   DataLength         = 376467
    rle bytes          : 376467 (magic + 376465 payload + checksum)
    chunks             : 189545
    decoded pixels     : 36806400 / 36806400  (8520x4320)
    ended on boundary  : True (stopped at payload byte 376466 of 376466)
    checksum           : stored 0x8c, computed 0x8c -> MATCH
    non-zero pixels    : 16747409 (45.5014%), max gray 255
    RESULT             : PASS

--- layer 2 @ +948480 ---
    +0   >H   Pause              = 0
    +2   >f   PausePositionZ     = 165
    +6   >f   PositionZ          = 0.15
    +10  >f   ExposureTime       = 35
    +14  >f   LightOffDelay      = 0
    +18  >f   WaitTimeAfterCure  = 0.5
    +22  >f   WaitTimeAfterLift  = 0
    +26  >f   WaitTimeBeforeCure = 0.5
    +30  >f   LiftHeight         = 0.05
    +34  >f   LiftSpeed          = 0.05
    +38  >f   LiftHeight2        = 0
    +42  >f   LiftSpeed2         = 0
    +46  >f   RetractHeight      = 0.05
    +50  >f   RetractSpeed       = 0.05
    +54  >f   RetractHeight2     = 0
    +58  >f   RetractSpeed2      = 0
    +62  >H   LightPWM           = 255
    +64  raw  Delimiter          = 0d 0a
    +66  >I   DataLength         = 376436
    rle bytes          : 376436 (magic + 376434 payload + checksum)
    chunks             : 189511
    decoded pixels     : 36806400 / 36806400  (8520x4320)
    ended on boundary  : True (stopped at payload byte 376435 of 376435)
    checksum           : stored 0xf1, computed 0xf1 -> MATCH
    non-zero pixels    : 16781011 (45.5926%), max gray 255
    RESULT             : PASS

--- layer 1829 @ +190970207 ---
    +0   >H   Pause              = 0
    +2   >f   PausePositionZ     = 165
    +6   >f   PositionZ          = 91.5
    +10  >f   ExposureTime       = 3.25
    +14  >f   LightOffDelay      = 0
    +18  >f   WaitTimeAfterCure  = 0.5
    +22  >f   WaitTimeAfterLift  = 0
    +26  >f   WaitTimeBeforeCure = 0.5
    +30  >f   LiftHeight         = 0.05
    +34  >f   LiftSpeed          = 0.05
    +38  >f   LiftHeight2        = 0
    +42  >f   LiftSpeed2         = 0
    +46  >f   RetractHeight      = 0.05
    +50  >f   RetractSpeed       = 0.05
    +54  >f   RetractHeight2     = 0
    +58  >f   RetractSpeed2      = 0
    +62  >H   LightPWM           = 255
    +64  raw  Delimiter          = 0d 0a
    +66  >I   DataLength         = 1013
    rle bytes          : 1013 (magic + 1011 payload + checksum)
    chunks             : 437
    decoded pixels     : 36806400 / 36806400  (8520x4320)
    ended on boundary  : True (stopped at payload byte 1012 of 1012)
    checksum           : stored 0x74, computed 0x74 -> MATCH
    non-zero pixels    : 5486 (0.0149%), max gray 255
    RESULT             : PASS

--- layer 1830 @ +190971292 ---
    +0   >H   Pause              = 0
    +2   >f   PausePositionZ     = 165
    +6   >f   PositionZ          = 91.55
    +10  >f   ExposureTime       = 3.25
    +14  >f   LightOffDelay      = 0
    +18  >f   WaitTimeAfterCure  = 0.5
    +22  >f   WaitTimeAfterLift  = 0
    +26  >f   WaitTimeBeforeCure = 0.5
    +30  >f   LiftHeight         = 0.05
    +34  >f   LiftSpeed          = 0.05
    +38  >f   LiftHeight2        = 0
    +42  >f   LiftSpeed2         = 0
    +46  >f   RetractHeight      = 0.05
    +50  >f   RetractSpeed       = 0.05
    +54  >f   RetractHeight2     = 0
    +58  >f   RetractSpeed2      = 0
    +62  >H   LightPWM           = 255
    +64  raw  Delimiter          = 0d 0a
    +66  >I   DataLength         = 493
    rle bytes          : 493 (magic + 491 payload + checksum)
    chunks             : 249
    decoded pixels     : 36806400 / 36806400  (8520x4320)
    ended on boundary  : True (stopped at payload byte 492 of 492)
    checksum           : stored 0xb7, computed 0xb7 -> MATCH
    non-zero pixels    : 1154 (0.0031%), max gray 255
    RESULT             : PASS

--- layer 1831 @ +190971857 ---
    +0   >H   Pause              = 0
    +2   >f   PausePositionZ     = 165
    +6   >f   PositionZ          = 91.6
    +10  >f   ExposureTime       = 3.25
    +14  >f   LightOffDelay      = 0
    +18  >f   WaitTimeAfterCure  = 0.5
    +22  >f   WaitTimeAfterLift  = 0
    +26  >f   WaitTimeBeforeCure = 0.5
    +30  >f   LiftHeight         = 0.05
    +34  >f   LiftSpeed          = 0.05
    +38  >f   LiftHeight2        = 0
    +42  >f   LiftSpeed2         = 0
    +46  >f   RetractHeight      = 0.05
    +50  >f   RetractSpeed       = 0.05
    +54  >f   RetractHeight2     = 0
    +58  >f   RetractSpeed2      = 0
    +62  >H   LightPWM           = 255
    +64  raw  Delimiter          = 0d 0a
    +66  >I   DataLength         = 23
    rle bytes          : 23 (magic + 21 payload + checksum)
    chunks             : 9
    decoded pixels     : 36806400 / 36806400  (8520x4320)
    ended on boundary  : True (stopped at payload byte 22 of 22)
    checksum           : stored 0xf7, computed 0xf7 -> MATCH
    non-zero pixels    : 11 (0.0000%), max gray 255
    RESULT             : PASS

=== SUMMARY ===
  All checks passed.
```

### RLE self-test (all four chunk types and all four length forms)

```
$ .venv/bin/python scripts/goo_probe.py --selftest
selftest diff_mode=True : 4271 bytes, 4096/4096 px, boundary=True, checksum=True, round-trip=True
selftest diff_mode=False: 4693 bytes, 4096/4096 px, boundary=True, checksum=True, round-trip=True
selftest 40,000,000-px zero run: chunk=30 26 25 a0 -> 40000000 px, checksum=True
```

(exit status 0)

### Encoder round trip

Re-implementing the encoder described above and re-encoding the *decoded* images
reproduces the file's RLE blobs byte-for-byte:

```
layer     0: orig   376392 bytes, re-encoded   376392 bytes, identical=True
layer     1: orig   376467 bytes, re-encoded   376467 bytes, identical=True
layer     2: orig   376436 bytes, re-encoded   376436 bytes, identical=True
layer   900: orig   117343 bytes, re-encoded   117343 bytes, identical=True
layer  1829: orig     1013 bytes, re-encoded     1013 bytes, identical=True
layer  1830: orig      493 bytes, re-encoded      493 bytes, identical=True
layer  1831: orig       23 bytes, re-encoded       23 bytes, identical=True
```
