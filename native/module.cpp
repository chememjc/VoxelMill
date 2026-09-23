#include <pybind11/pybind11.h>
#include <memory>
#include "parallel.hpp"
namespace py = pybind11;
void bind_mesh(py::module_&);
void bind_raster(py::module_&);
void bind_intersections(py::module_&);
void bind_voxel(py::module_&);
void bind_distance(py::module_&);
void bind_runs(py::module_&);
void bind_edt(py::module_&);
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
 bind_runs(m);
 bind_edt(m);
#ifdef VOXELMILL_CUDA
 bind_cuda(m);
#endif
// A worker ceiling for native parallel loops, held for the lifetime of the
// Python object. It exists with or without TBB (see parallel.hpp).
 py::class_<voxelmill::WorkerLimit>(m,"WorkerLimit")
 .def(py::init<size_t>(), py::arg("workers"));
#ifdef VOXELMILL_TBB
 m.attr("HAS_TBB") = true;
#else
 m.attr("HAS_TBB") = false;
#endif
}
