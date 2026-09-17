// Classic CTB v3 7-bit grayscale run codec.
#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <vector>
namespace py = pybind11;
namespace {
constexpr size_t MAX_PIXELS = 100000000u;

void emit(std::vector<uint8_t>& out, uint8_t color, uint32_t count) {
 if (!count || count > 0x0fffffffu) throw std::invalid_argument("CTB run exceeds format limit");
 if (count == 1) { out.push_back(color); return; }
 out.push_back(uint8_t(color | 0x80));
 if (count <= 0x7f) out.push_back(uint8_t(count));
 else if (count <= 0x3fff) { out.push_back(uint8_t(0x80 | (count >> 8))); out.push_back(uint8_t(count)); }
 else if (count <= 0x1fffff) { out.push_back(uint8_t(0xc0 | (count >> 16))); out.push_back(uint8_t(count >> 8)); out.push_back(uint8_t(count)); }
 else { out.push_back(uint8_t(0xe0 | (count >> 24))); out.push_back(uint8_t(count >> 16)); out.push_back(uint8_t(count >> 8)); out.push_back(uint8_t(count)); }
}

py::bytes encode(py::array_t<uint8_t, py::array::c_style> image) {
 auto info=image.request();
 if(info.ndim!=2) throw std::invalid_argument("CTB layer must be two dimensional");
 size_t h=size_t(info.shape[0]), w=size_t(info.shape[1]);
 if(!h || !w || h>MAX_PIXELS/w) throw std::invalid_argument("CTB image exceeds encoder limit");
 const uint8_t* p=static_cast<const uint8_t*>(info.ptr); size_t total=h*w;
 std::vector<uint8_t> out; out.reserve(total/64+64);
 { py::gil_scoped_release release; size_t i=0;
   while(i<total) { uint8_t c=uint8_t(p[i]>>1); size_t end=i+1;
    while(end<total && uint8_t(p[end]>>1)==c) ++end;
    size_t remaining=end-i; while(remaining) { uint32_t n=uint32_t(std::min<size_t>(remaining,0x0fffffffu)); emit(out,c,n); remaining-=n; }
    i=end;
   }
 }
 return py::bytes(reinterpret_cast<const char*>(out.data()),out.size());
}

py::array_t<uint8_t> decode(py::buffer blob, int width, int height) {
 auto info=blob.request();
 if(info.ndim!=1 || info.itemsize!=1 || info.strides[0]!=1) throw std::invalid_argument("CTB blob must be contiguous bytes");
 if(width<1 || height<1 || size_t(width)>MAX_PIXELS/size_t(height)) throw std::invalid_argument("CTB dimensions exceed decoder limit");
 size_t total=size_t(width)*size_t(height), size=size_t(info.size), pos=0, i=0;
 const uint8_t* data=static_cast<const uint8_t*>(info.ptr);
 py::array_t<uint8_t> result({height,width}); uint8_t* out=result.mutable_data();
 { py::gil_scoped_release release;
   while(i<size) { uint8_t code=data[i++], color=uint8_t(code&0x7f); uint32_t count=1;
    if(code&0x80) { if(i>=size) throw std::invalid_argument("CTB run length truncated"); uint8_t first=data[i++];
     if(!(first&0x80)) count=first;
     else if((first&0xc0)==0x80) { if(i>=size) throw std::invalid_argument("CTB run length truncated"); count=uint32_t(first&0x3f)<<8|data[i++]; }
     else if((first&0xe0)==0xc0) { if(i+1>=size) throw std::invalid_argument("CTB run length truncated"); count=uint32_t(first&0x1f)<<16|uint32_t(data[i])<<8|data[i+1]; i+=2; }
     else if((first&0xf0)==0xe0) { if(i+2>=size) throw std::invalid_argument("CTB run length truncated"); count=uint32_t(first&0x0f)<<24|uint32_t(data[i])<<16|uint32_t(data[i+1])<<8|data[i+2]; i+=3; }
     else throw std::invalid_argument("invalid CTB run prefix");
     if(count<2) throw std::invalid_argument("CTB run marker encodes fewer than two pixels");
    }
    if(count>total-pos) throw std::invalid_argument("CTB layer exceeds declared dimensions");
    std::memset(out+pos, color ? uint8_t((color<<1)|1) : 0, count); pos+=count;
   }
   if(pos!=total) throw std::invalid_argument("CTB layer does not fill declared dimensions");
 }
 return result;
}
}
void bind_ctb(py::module_& m) {
 m.def("ctb_encode_layer",&encode,py::arg("image"));
 m.def("ctb_decode_layer",&decode,py::arg("blob"),py::arg("width"),py::arg("height"));
}
