// Bidirectional surface-distance verification. Distance to a closed set is
// 1-Lipschitz: d(center) + covering radius bounds every point of a triangle.
// Adaptive longest-edge subdivision certifies the requested tolerance without
// mistaking vertex samples or voxel pitch for a Hausdorff guarantee.
//
// Nearest-surface queries run against the BVH in geom.hpp. That replaced
// CGAL's AABB_tree, which is GPL-3.0-or-later and incompatible with this
// project's MIT license; see licenses/THIRD-PARTY.md.
#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include "geom.hpp"
#include <array>
#include <vector>
#include <cmath>
#include <cstring>
namespace py=pybind11;
namespace {
using vm::Vec3;
using Triangle=vm::Triangle;
struct Input {
 py::buffer_info b;
 explicit Input(const py::array&a):b(a.request()) {
  if(b.ndim!=3||b.shape[1]!=3||b.shape[2]!=3||a.dtype().kind()!='f'||(b.itemsize!=4&&b.itemsize!=8)||!a.dtype().attr("isnative").cast<bool>())throw std::invalid_argument("Expected native floating triangles (n,3,3)");
 }
 Triangle triangle(size_t i)const {
  Triangle t;
  for(int j=0;j<3;j++) {double c[3];for(int k=0;k<3;k++) {auto q=static_cast<char*>(b.ptr)+i*b.strides[0]+j*b.strides[1]+k*b.strides[2];if(b.itemsize==4){float v;std::memcpy(&v,q,4);c[k]=v;}else std::memcpy(&c[k],q,8);if(!std::isfinite(c[k]))throw std::invalid_argument("Nonfinite surface coordinate");}t.v[j]=Vec3{c[0],c[1],c[2]};}
  return t;
 }
};
struct Result {bool passed=true;bool unresolved=false;double sampled=0,upper=0;size_t queries=0,skipped=0;};
Result directed(const Input &from,const Input &to,double tolerance,const py::object&cb) {
 Result result;
 std::vector<Triangle> target;target.reserve(to.b.shape[0]);
 for(size_t i=0;i<size_t(to.b.shape[0]);i++){auto t=to.triangle(i);if(!t.degenerate())target.push_back(t);else result.skipped++;}
 if(target.empty())throw std::invalid_argument("Distance target has no nondegenerate triangles");
 std::vector<vm::Aabb> boxes;boxes.reserve(target.size());
 for(const auto&t:target)boxes.push_back(vm::triangle_bounds(t));
 vm::Bvh tree;tree.build(boxes);
 struct Work {Triangle triangle;int depth;};std::vector<Work> stack;
 for(size_t i=0;i<size_t(from.b.shape[0]);i++) {
  if(i%4096==0&&!cb.is_none()){py::gil_scoped_acquire lock;cb(i,size_t(from.b.shape[0]));}
  stack.push_back({from.triangle(i),0});
  while(!stack.empty()) {
   auto work=stack.back();stack.pop_back();auto&t=work.triangle;
   Vec3 center{(t[0].x+t[1].x+t[2].x)/3,(t[0].y+t[1].y+t[2].y)/3,(t[0].z+t[1].z+t[2].z)/3};
   double d=std::sqrt(tree.nearest_squared(center,[&](uint32_t j){return vm::point_triangle_squared_distance(center,target[j]);}));result.queries++;
   result.sampled=std::max(result.sampled,d);
   if(d>tolerance+1e-10){result.passed=false;return result;}
   double cover=0;for(int j=0;j<3;j++)cover=std::max(cover,std::sqrt(vm::squared_distance(center,t[j])));
   if(d+cover<=tolerance+1e-10){result.upper=std::max(result.upper,d+cover);continue;}
   if(result.queries>100000000){result.passed=false;result.unresolved=true;return result;}
   if(work.depth>=24){result.passed=false;result.unresolved=true;continue;}
   int longest=0;double length=-1;for(int j=0;j<3;j++){double l=vm::squared_distance(t[j],t[(j+1)%3]);if(l>length){length=l;longest=j;}}
   int a=longest,b=(a+1)%3,c=(a+2)%3;Vec3 mid=vm::midpoint(t[a],t[b]);
   stack.push_back({Triangle{{t[a],mid,t[c]}},work.depth+1});stack.push_back({Triangle{{mid,t[b],t[c]}},work.depth+1});
  }
 }
 return result;
}
py::dict verify(const py::array&a,const py::array&b,double tolerance,const py::object&cb) {
 if(!std::isfinite(tolerance)||tolerance<=0)throw std::invalid_argument("Tolerance must be positive and finite");
 Input original(a),repaired(b);Result forward,reverse;
 {py::gil_scoped_release release;forward=directed(original,repaired,tolerance,cb);if(forward.passed)reverse=directed(repaired,original,tolerance,cb);}
 py::dict out;bool pass=forward.passed&&reverse.passed;
 out["passed"]=pass;out["status"]=pass?"certified":(forward.unresolved||reverse.unresolved?"inconclusive":"exceeds_limit");
 out["sampled_lower_bound_mm"]=std::max(forward.sampled,reverse.sampled);
 out["certified_upper_bound_mm"]=pass?py::cast(std::max(forward.upper,reverse.upper)):py::none();
 out["distance_queries"]=forward.queries+reverse.queries;out["target_degenerate_triangles_skipped"]=forward.skipped+reverse.skipped;
 out["method"]="bidirectional adaptive triangle covering; BVH nearest-surface distance; numerical tolerance 1e-10 mm";
 return out;
}
}
void bind_distance(py::module_ &m){m.def("verify_surface_deviation",&verify,py::arg("original"),py::arg("repaired"),py::arg("tolerance_mm"),py::arg("callback")=py::none());}
