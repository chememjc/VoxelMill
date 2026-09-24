// GOO v3.0 layer image codec.
//
// Layout, chunk types and the checksum implement the published ELEGOO GOO V3.0
// specification as transcribed in docs/goo-format.md, and were verified byte
// for byte against the reference file. No code is taken from any other
// implementation. UVtools was consulted only as interoperability evidence,
// where the official document contradicts itself; docs/goo-format.md records
// each such point. Producing the same chunk choice as other slicers for
// identical pixels is a format-compatibility goal, not a derivation, and is
// evidence of compatibility rather than of hardware behavior.
#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>
namespace py = pybind11;
namespace {

constexpr uint8_t LAYER_MAGIC = 0x55;
constexpr uint32_t MAX_RUN = 0x0fffffffu;
// Current Mars 5 Ultra LCD layers contain 36,806,400 pixels.  The bound keeps
// malformed GOO headers from turning a decoder call into an arbitrary giant
// allocation while leaving room for larger known mono panels.
constexpr size_t MAX_LAYER_PIXELS = 100000000u;

size_t checked_pixels(int width, int height) {
 if (width < 1 || height < 1) throw std::invalid_argument("image dimensions must be positive");
 const size_t w = size_t(width), h = size_t(height);
 if (w > MAX_LAYER_PIXELS / h) throw std::invalid_argument("image dimensions exceed GOO decoder limit");
 return w * h;
}

void emit_run(std::vector<uint8_t> &out, int value, uint32_t stride, int previous) {
 const int delta = value - previous;
 const int magnitude = delta < 0 ? -delta : delta;
 if (magnitude <= 15 && stride <= 255 && value > 0 && value < 0xff) {
  uint8_t head = 0x80 | uint8_t(magnitude & 0x0f);
  if (stride > 1) head |= 0x10;
  if (delta < 0) head |= 0x20;
  out.push_back(head);
  if (stride > 1) out.push_back(uint8_t(stride));
  return;
 }
 const uint8_t type = value == 0 ? 0x00 : (value == 0xff ? 0xc0 : 0x40);
 uint8_t form;
 if (stride <= 0xf) form = 0;
 else if (stride <= 0xfff) form = 1;
 else if (stride <= 0xfffff) form = 2;
 else form = 3;
 out.push_back(uint8_t(type | (form << 4) | (stride & 0x0f)));
 if (type == 0x40) out.push_back(uint8_t(value));      // gray byte precedes lengths
 if (form == 1) out.push_back(uint8_t((stride >> 4) & 0xff));
 else if (form == 2) { out.push_back(uint8_t((stride >> 12) & 0xff)); out.push_back(uint8_t((stride >> 4) & 0xff)); }
 else if (form == 3) { out.push_back(uint8_t((stride >> 20) & 0xff)); out.push_back(uint8_t((stride >> 12) & 0xff)); out.push_back(uint8_t((stride >> 4) & 0xff)); }
}

// End of the run of `value` starting at `index`, eight bytes at a time where
// possible. Layers are mostly long dark runs, so this is the encoder's hot loop.
inline size_t run_end(const uint8_t *pixels, size_t index, size_t total, uint8_t value) {
 const uint64_t pattern = 0x0101010101010101ull * value;
 while (index + 8 <= total) {
  uint64_t word;
  std::memcpy(&word, pixels + index, 8);
  if (word != pattern) break;
  index += 8;
 }
 while (index < total && pixels[index] == value) ++index;
 return index;
}

// Accumulates (value, length) pieces, merging equal neighbours, and emits
// merged runs exactly as a pixel-by-pixel scan of the same frame would.
struct RunEmitter {
 std::vector<uint8_t> &out;
 int previous = 0;
 int value = -1;
 size_t length = 0;
 void push(uint8_t v, size_t n) {
  if (!n) return;
  if (int(v) == value) { length += n; return; }
  flush();
  value = v;
  length = n;
 }
 void flush() {
  while (length) {
   const uint32_t stride = uint32_t(length < MAX_RUN ? length : MAX_RUN);
   emit_run(out, value, stride, previous);
   previous = value;
   length -= stride;
  }
 }
};

void finish_blob(std::vector<uint8_t> &out) {
 uint8_t sum = 0;
 for (size_t i = 1; i < out.size(); ++i) sum = uint8_t(sum + out[i]);
 out.push_back(uint8_t(~sum));
}

py::bytes encode_layer(py::array_t<uint8_t, py::array::c_style | py::array::forcecast> image) {
 auto info = image.request();
 if (info.ndim != 2) throw std::invalid_argument("layer image must be two dimensional");
 const size_t height = size_t(info.shape[0]), width = size_t(info.shape[1]);
 if (!height || !width || height > MAX_LAYER_PIXELS / width)
  throw std::invalid_argument("layer image dimensions exceed GOO encoder limit");
 const size_t total = height * width;
 if (!total) throw std::invalid_argument("layer image must not be empty");
 const uint8_t *pixels = static_cast<const uint8_t *>(info.ptr);
 std::vector<uint8_t> out;
 out.reserve(total / 64 + 64);
 out.push_back(LAYER_MAGIC);
 {
  py::gil_scoped_release release;
  RunEmitter runs{out};
  size_t index = 0;
  while (index < total) {
   const uint8_t value = pixels[index];
   const size_t end = run_end(pixels, index, total, value);
   runs.push(value, end - index);
   index = end;
  }
  runs.flush();
  finish_blob(out);
 }
 return py::bytes(reinterpret_cast<const char *>(out.data()), out.size());
}

struct Placement {
 const uint8_t *pixels;  // C-contiguous crop
 size_t rows, columns, row, column, width, height;
 // LCD intensity of each crop value. With binary scaling, a crop holding only
 // 0/1 exposes 1 as 255 (full intensity); any value above 1 means grayscale
 // coverage, and the crop is then taken as-is.
 std::array<uint8_t, 256> intensity;
 bool binary_scale;
};

Placement placement(const py::buffer_info &crop, int row, int column, int width, int height,
                    bool binary_scale) {
 if (crop.ndim != 2) throw std::invalid_argument("layer crop must be two dimensional");
 checked_pixels(width, height);
 Placement p{static_cast<const uint8_t *>(crop.ptr), size_t(crop.shape[0]), size_t(crop.shape[1]),
             size_t(row), size_t(column), size_t(width), size_t(height), {}, false};
 if (row < 0 || column < 0 || p.row + p.rows > p.height || p.column + p.columns > p.width)
  throw std::invalid_argument("layer crop falls outside the frame");
 for (int v = 0; v < 256; ++v) p.intensity[size_t(v)] = uint8_t(v);
 p.binary_scale = binary_scale;
 return p;
}

// Resolve binary scaling. A branch-free max reduction, so the compiler can
// vectorize it; callers run it with the GIL released.
void resolve_intensity(Placement &p) {
 if (!p.binary_scale) return;
 const size_t count = p.rows * p.columns;
 uint8_t largest = 0;
 for (size_t i = 0; i < count; ++i) largest = std::max(largest, p.pixels[i]);
 if (largest <= 1) p.intensity[1] = 0xff;
}

// Byte-identical to encode_layer on the full frame with `crop` pasted at
// (row, column) and every other pixel dark, without building that frame.
py::bytes encode_placed(py::array_t<uint8_t, py::array::c_style | py::array::forcecast> crop,
                        int row, int column, int width, int height, bool binary_scale) {
 Placement p = placement(crop.request(), row, column, width, height, binary_scale);
 std::vector<uint8_t> out;
 out.reserve(p.rows * p.columns / 16 + 256);
 out.push_back(LAYER_MAGIC);
 {
  py::gil_scoped_release release;
  resolve_intensity(p);
  RunEmitter runs{out};
  const size_t total = p.width * p.height;
  if (!p.rows || !p.columns) {
   runs.push(0, total);
  } else {
   runs.push(0, p.row * p.width + p.column);
   const size_t after = p.width - (p.column + p.columns);
   for (size_t r = 0; r < p.rows; ++r) {
    const uint8_t *line = p.pixels + r * p.columns;
    size_t index = 0;
    while (index < p.columns) {
     const uint8_t value = line[index];
     const size_t end = run_end(line, index, p.columns, value);
     runs.push(p.intensity[value], end - index);
     index = end;
    }
    runs.push(0, after + (r + 1 < p.rows ? p.column : (p.height - p.row - p.rows) * p.width));
   }
  }
  runs.flush();
  finish_blob(out);
 }
 return py::bytes(reinterpret_cast<const char *>(out.data()), out.size());
}

// Walks a layer blob's chunk stream, validating framing and checksum exactly
// as the decoder always has, and hands each run to `sink(color, pixel, stride)`.
template<class Sink>
void walk_layer(const uint8_t *data, size_t size, size_t total, Sink &&sink) {
 if (size < 3) throw std::invalid_argument("layer blob is truncated");
 if (data[0] != LAYER_MAGIC) throw std::invalid_argument("layer blob does not start with 0x55");
 const size_t last = size - 1;
 uint8_t sum = 0;
 for (size_t i = 1; i < last; ++i) sum = uint8_t(sum + data[i]);
 if (uint8_t(~sum) != data[last]) throw std::invalid_argument("layer checksum mismatch");
 size_t pixel = 0, i = 1;
 int color = 0;
 while (i < last) {
  const uint8_t head = data[i];
  const int type = head >> 6;
  uint64_t stride = 0;
  size_t base = i;
  if (type == 0) color = 0x00;
  else if (type == 1) {
   if (++i >= last) throw std::invalid_argument("gray chunk truncated");
   color = data[i];
   base = i;
  } else if (type == 2) {
   const int mode = (head >> 4) & 0x3;
   const int delta = head & 0x0f;
   color = (mode == 0 || mode == 1) ? ((color + delta) & 0xff) : ((color - delta) & 0xff);
   if (mode == 1 || mode == 3) {
    if (++i >= last) throw std::invalid_argument("difference chunk truncated");
    stride = data[i];
   } else stride = 1;
  } else color = 0xff;
  if (type != 2) {
   const int form = (head >> 4) & 0x3;
   if (base + size_t(form) >= last) throw std::invalid_argument("run-length chunk truncated");
   if (form == 0) stride = head & 0x0f;
   else if (form == 1) { stride = (uint64_t(data[base + 1]) << 4) | (head & 0x0f); i = base + 1; }
   else if (form == 2) { stride = (uint64_t(data[base + 1]) << 12) | (uint64_t(data[base + 2]) << 4) | (head & 0x0f); i = base + 2; }
   else { stride = (uint64_t(data[base + 1]) << 20) | (uint64_t(data[base + 2]) << 12) | (uint64_t(data[base + 3]) << 4) | (head & 0x0f); i = base + 3; }
  }
  if (!stride) throw std::invalid_argument("zero-length run");
  if (pixel + stride > total) throw std::invalid_argument("run overflows the image");
  sink(uint8_t(color), pixel, size_t(stride));
  pixel += size_t(stride);
  ++i;
 }
 if (i != last) throw std::invalid_argument("chunk stream did not end on the checksum byte");
 if (pixel != total) throw std::invalid_argument("decoded pixel count differs from the image size");
}

py::buffer_info flat_blob(py::buffer &blob) {
 py::buffer_info info = blob.request();
 if (info.ndim != 1 || info.itemsize != 1 || info.strides[0] != 1)
  throw std::invalid_argument("layer blob must be a contiguous flat byte buffer");
 return info;
}

py::array_t<uint8_t> decode_layer(py::buffer blob, int width, int height) {
 py::buffer_info info = flat_blob(blob);
 const size_t total = checked_pixels(width, height);
 const uint8_t *data = static_cast<const uint8_t *>(info.ptr);
 const size_t size = size_t(info.size);
 if (size < 3) throw std::invalid_argument("layer blob is truncated");
 if (data[0] != LAYER_MAGIC) throw std::invalid_argument("layer blob does not start with 0x55");
 py::array_t<uint8_t> image({py::ssize_t(height), py::ssize_t(width)});
 uint8_t *out = image.mutable_data();
 {
  py::gil_scoped_release release;
  walk_layer(data, size, total, [&](uint8_t color, size_t pixel, size_t stride) {
   std::memset(out + pixel, color, stride);
  });
 }
 return image;
}

// Decodes a blob and counts pixels that differ by more than `atol` from the
// frame `crop` pasted at (row, column) on a dark panel, without materializing
// either frame. Every framing and checksum error raises, as in decode_layer.
size_t verify_placed(py::buffer blob, py::array_t<uint8_t, py::array::c_style | py::array::forcecast> crop,
                     int row, int column, int width, int height, int atol, bool binary_scale) {
 if (atol < 0 || atol > 255) throw std::invalid_argument("atol must be 0..255");
 py::buffer_info info = flat_blob(blob);
 Placement p = placement(crop.request(), row, column, width, height, binary_scale);
 const size_t total = p.width * p.height;
 const uint8_t *data = static_cast<const uint8_t *>(info.ptr);
 size_t mismatches = 0;
 {
  py::gil_scoped_release release;
  resolve_intensity(p);
  // The raw crop value exposed at each intensity. The mapping is one-to-one
  // over the values a crop can hold, so an exact check counts equal bytes,
  // a loop the compiler vectorizes.
  std::array<int, 256> raw_for;
  raw_for.fill(-1);
  for (int v = 0; v < 256; ++v)
   if (raw_for[p.intensity[size_t(v)]] < 0) raw_for[p.intensity[size_t(v)]] = v;
  walk_layer(data, size_t(info.size), total, [&](uint8_t color, size_t pixel, size_t stride) {
   size_t end = pixel + stride;
   while (pixel < end) {
    const size_t y = pixel / p.width, x = pixel % p.width;
    const size_t line_end = std::min(end, (y + 1) * p.width);
    const size_t span = line_end - pixel;
    const bool lit = int(color) > atol;  // differs from a dark expected pixel
    if (y < p.row || y >= p.row + p.rows) {
     if (lit) mismatches += span;
    } else {
     // Dark columns either side of the crop, then the crop itself.
     const size_t x_end = x + span;
     const size_t lo = std::max(x, p.column), hi = std::min(x_end, p.column + p.columns);
     if (lit) mismatches += span - (hi > lo ? hi - lo : 0);
     if (hi > lo) {
      const uint8_t *line = p.pixels + (y - p.row) * p.columns - p.column;
      if (atol == 0) {
       const int raw = raw_for[color];
       size_t equal = 0;
       if (raw >= 0) {
        const uint8_t want = uint8_t(raw);
        for (size_t c = lo; c < hi; ++c) equal += line[c] == want;
       }
       mismatches += (hi - lo) - equal;
      } else {
       for (size_t c = lo; c < hi; ++c) {
        const int difference = int(p.intensity[line[c]]) - int(color);
        mismatches += (difference < 0 ? -difference : difference) > atol;
       }
      }
     }
    }
    pixel = line_end;
   }
  });
 }
 return mismatches;
}
}
void bind_goo(py::module_ &m) {
 m.def("goo_encode_layer", &encode_layer, py::arg("image"),
       "Encode an (h,w) uint8 image as a GOO v3 layer blob including magic and checksum");
 m.def("goo_decode_layer", &decode_layer, py::arg("blob"), py::arg("width"), py::arg("height"),
       "Decode a GOO v3 layer blob; every framing and checksum error raises");
 m.def("goo_encode_placed", &encode_placed, py::arg("crop"), py::arg("row"), py::arg("column"),
       py::arg("width"), py::arg("height"), py::arg("binary_scale") = false,
       "Encode a (width,height) layer that is dark except `crop` at (row, column); "
       "byte-identical to goo_encode_layer on that full frame. binary_scale exposes a "
       "crop holding only 0/1 at 0/255");
 m.def("goo_verify_placed", &verify_placed, py::arg("blob"), py::arg("crop"), py::arg("row"),
       py::arg("column"), py::arg("width"), py::arg("height"), py::arg("atol") = 0,
       py::arg("binary_scale") = false,
       "Decode a GOO v3 layer blob and count pixels that differ by more than atol from `crop` "
       "at (row, column) on a dark frame; every framing and checksum error raises");
}
