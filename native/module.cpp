#include <pybind11/pybind11.h>
#ifdef VOXELMILL_TBB
#include <tbb/global_control.h>
#endif
#include <memory>
namespace py = pybind11;
void bind_mesh(py::module_&);
void bind_raster(py::module_&);
void bind_intersections(py::module_&);
void bind_voxel(py::module_&);
void bind_distance(py::module_&);
void bind_goo(py::module_&);
void bind_ctb(py::module_&);
#ifdef VOXELMILL_CUDA
void bind_cuda(py::module_&);
#endif
PYBIND11_MODULE(_native, m) {
 m.doc() = "Bounded native geometry and single-layer scan conversion";
 bind_mesh(m);
 bind_intersections(m);
 bind_raster(m);
 bind_voxel(m);
 bind_goo(m);
 bind_ctb(m);
 bind_distance(m);
#ifdef VOXELMILL_CUDA
 bind_cuda(m);
#endif
#ifdef VOXELMILL_TBB
 py::class_<tbb::global_control>(m,"WorkerLimit")
 .def(py::init([](size_t n) {
   if(n < 1 || n > 32) throw std::invalid_argument("workers must be 1..32");
   return std::make_unique<tbb::global_control>(tbb::global_control::max_allowed_parallelism,n);
 }));
#endif
}
