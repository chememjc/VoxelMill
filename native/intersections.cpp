// Self-intersection scan. Adjacency that a closed surface legitimately
// contains -- a shared edge between two faces, a shared vertex in a fan -- is
// not an intersection, so candidate pairs are classified by how much of their
// vertex sets coincide before any overlap test runs.
//
// Broad phase and every predicate come from geom.hpp. They replaced CGAL's
// box_self_intersection_d and do_intersect, which are GPL-3.0-or-later and
// incompatible with this project's MIT license; see licenses/THIRD-PARTY.md.
// The predicates remain exact: their determinants are accumulated in
// double-double arithmetic, because the coplanar and same-side tests below
// turn on a determinant being exactly zero.
#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include "geom.hpp"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <vector>
namespace py=pybind11;
namespace {
using Triangle=vm::Triangle;
bool improper(const Triangle&a,const Triangle&b){
 int ai[3],bi[3],n=0;for(int i=0;i<3;i++)for(int j=0;j<3;j++)if(a[i]==b[j]){ai[n]=i;bi[n]=j;n++;}
 if(n==3)return true; // Coincident duplicate triangles are not mere adjacency.
 if(n==2){int x=3-ai[0]-ai[1],y=3-bi[0]-bi[1];return vm::coplanar(a[ai[0]],a[ai[1]],a[x],b[y])&&vm::coplanar_orientation(a[ai[0]],a[ai[1]],a[x],b[y])==1;}
 if(n==1)return vm::segment_meets_triangle(b[(bi[0]+1)%3],b[(bi[0]+2)%3],a)||vm::segment_meets_triangle(a[(ai[0]+1)%3],a[(ai[0]+2)%3],b);
 return vm::triangles_meet(a,b);
}
py::dict inspect_intersections(const py::array &arr,const py::object &cb){
 auto info=arr.request();if(info.ndim!=3||info.shape[1]!=3||info.shape[2]!=3||(arr.dtype().kind()!='f' || (info.itemsize!=4 && info.itemsize!=8) || !arr.dtype().attr("isnative").cast<bool>()))throw std::invalid_argument("triangles must be a native-endian float32 or float64 array of shape (n,3,3)");
 size_t n=info.shape[0],candidates=0,count=0,invalid=0;std::vector<std::pair<uint32_t,uint32_t>> examples;
 if(n>UINT32_MAX)throw std::invalid_argument("Too many triangles");
 auto tick=[&](const char*stage,size_t done,size_t total){if(!cb.is_none()){py::gil_scoped_acquire lock;cb(stage,done,total);}};
 {py::gil_scoped_release release;
 // Coordinates are evaluated exactly as stored: float32 input is widened, never rounded.
 auto coord=[&](size_t t,int v,int c)->double{auto ptr=static_cast<char*>(info.ptr)+t*info.strides[0]+v*info.strides[1]+c*info.strides[2];if(info.itemsize==4){float value;std::memcpy(&value,ptr,4);return value;}double value;std::memcpy(&value,ptr,8);return value;};
 auto triangle=[&](size_t t){Triangle tri;for(int v=0;v<3;v++)tri.v[v]=vm::Vec3{coord(t,v,0),coord(t,v,1),coord(t,v,2)};return tri;};
 // Keep only the triangles a predicate can answer for, remembering each one's
 // index in the caller's array so reported pairs stay meaningful.
 std::vector<Triangle> kept;std::vector<uint32_t> handle;std::vector<vm::Aabb> boxes;
 kept.reserve(n);handle.reserve(n);boxes.reserve(n);
 for(size_t t=0;t<n;t++){
  if(t%65536==0)tick("intersection_boxes",t,n);
  bool finite=true;for(int v=0;v<3;v++)for(int c=0;c<3;c++)finite=finite&&std::isfinite(coord(t,v,c));
  if(!finite){invalid++;continue;}auto tri=triangle(t);if(tri.degenerate()){invalid++;continue;}
  kept.push_back(tri);handle.push_back(uint32_t(t));boxes.push_back(vm::triangle_bounds(tri));
 }
 vm::Bvh tree;tree.build(boxes);
 tick("self_intersections",0,n);
 // Each unordered pair is offered once, by taking only the partners that sort
 // after the triangle being queried.
 for(size_t i=0;i<kept.size();i++){
  if(i%65536==0)tick("self_intersections",i,kept.size());
  tree.overlaps(boxes[i],[&](uint32_t j){
   if(j<=i)return;
   if(++candidates%65536==0)tick("intersection_candidates",candidates,0);
   if(!improper(kept[i],kept[j]))return;
   count++;auto pair=std::make_pair(std::min(handle[i],handle[j]),std::max(handle[i],handle[j]));
   if(examples.size()<100){examples.push_back(pair);std::push_heap(examples.begin(),examples.end());}
   else if(pair<examples.front()){std::pop_heap(examples.begin(),examples.end());examples.back()=pair;std::push_heap(examples.begin(),examples.end());}
  });
 }
 tick("self_intersections",n,n);
 }
 std::sort(examples.begin(),examples.end());py::list pairs;for(auto pair:examples)pairs.append(py::make_tuple(pair.first,pair.second));py::dict out;out["self_intersections_status"]=invalid?"partial_invalid_triangles":"complete";out["self_intersections"]=count;out["intersection_candidate_pairs"]=candidates;out["intersection_examples"]=pairs;out["intersection_skipped_invalid_triangles"]=invalid;out["intersection_method"]="exact double-double predicates; all BVH candidates; legal shared subfaces excluded";return out;
}
}
void bind_intersections(py::module_ &m){m.def("inspect_intersections",&inspect_intersections,py::arg("triangles"),py::arg("callback")=py::none());}
