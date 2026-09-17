#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <cstdint>
#include <stdexcept>
namespace py=pybind11;extern "C" const char*voxelmill_cuda_status(int*);extern "C" const char*voxelmill_cuda_morphology(const uint8_t*,uint8_t*,int,int,int,int,bool,int);
static py::dict status(){int c=0;const char*e=voxelmill_cuda_status(&c);py::dict r;r["compiled"]=true;r["available"]=!e&&c>0;r["device_count"]=e?0:c;r["reason"]=e?py::cast(std::string(e)):(c?py::none():py::cast("no CUDA devices"));return r;}
static py::array_t<uint8_t> apply(py::array_t<uint8_t,py::array::c_style|py::array::forcecast>a,int rx,int ry,bool erode,int dev){auto b=a.request();if(b.ndim!=2||rx<0||ry<0)throw std::invalid_argument("invalid morphology input or radius");int h=b.shape[0],w=b.shape[1];py::array_t<uint8_t>o({h,w});const char*e=voxelmill_cuda_morphology((uint8_t*)b.ptr,o.mutable_data(),w,h,rx,ry,erode,dev);if(e)throw std::runtime_error(e);return o;}
void bind_cuda(py::module_&m){m.def("cuda_status",&status);m.def("cuda_binary_morphology",&apply,py::arg("mask"),py::arg("rx"),py::arg("ry"),py::arg("erode"),py::arg("device")=0);}
