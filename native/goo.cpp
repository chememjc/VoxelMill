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
  size_t index = 0;
  int previous = 0;
  while (index < total) {
   const uint8_t value = pixels[index];
   size_t end = index;
   while (end < total && pixels[end] == value) ++end;
   size_t run = end - index;
   while (run) {
    const uint32_t stride = uint32_t(run < MAX_RUN ? run : MAX_RUN);
    emit_run(out, value, stride, previous);
    previous = value;
    run -= stride;
   }
   index = end;
  }
  uint8_t sum = 0;
  for (size_t i = 1; i < out.size(); ++i) sum = uint8_t(sum + out[i]);
  out.push_back(uint8_t(~sum));
 }
 return py::bytes(reinterpret_cast<const char *>(out.data()), out.size());
}

py::array_t<uint8_t> decode_layer(py::buffer blob, int width, int height) {
 py::buffer_info info = blob.request();
 if (info.ndim != 1 || info.itemsize != 1 || info.strides[0] != 1)
  throw std::invalid_argument("layer blob must be a contiguous flat byte buffer");
 const size_t total = checked_pixels(width, height);
 const size_t size = size_t(info.size);
 const uint8_t *data = static_cast<const uint8_t *>(info.ptr);
 if (size < 3) throw std::invalid_argument("layer blob is truncated");
 if (data[0] != LAYER_MAGIC) throw std::invalid_argument("layer blob does not start with 0x55");
 const size_t last = size - 1;
 py::array_t<uint8_t> image({py::ssize_t(height), py::ssize_t(width)});
 uint8_t *out = image.mutable_data();
 {
  py::gil_scoped_release release;
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
   std::memset(out + pixel, color, size_t(stride));
   pixel += size_t(stride);
   ++i;
  }
  if (i != last) throw std::invalid_argument("chunk stream did not end on the checksum byte");
  if (pixel != total) throw std::invalid_argument("decoded pixel count differs from the image size");
 }
 return image;
}
}
void bind_goo(py::module_ &m) {
 m.def("goo_encode_layer", &encode_layer, py::arg("image"),
       "Encode an (h,w) uint8 image as a GOO v3 layer blob including magic and checksum");
 m.def("goo_decode_layer", &decode_layer, py::arg("blob"), py::arg("width"), py::arg("height"),
       "Decode a GOO v3 layer blob; every framing and checksum error raises");
}
