#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>
namespace py = pybind11;

// Sequential active-triangle rasterizer. Single uint8 layer only; no 3D volume.
//
// Fill rules. "nonzero" accumulates the signed crossing direction of the
// cross-section contour and fills where the winding number differs from zero.
// It reproduces "evenodd" exactly for a closed, consistently oriented,
// non-self-intersecting solid, and additionally tolerates inverted components,
// overlapping shells and self-intersections, all of which every supplied
// original fixture contains. Neither rule invents material across a hole: a row
// whose winding does not return to zero is reported, never filled to the edge.
class Rasterizer {
 py::array data;
 py::buffer_info b;
 std::vector<uint32_t> entering, leaving, active;
 std::vector<int32_t> slot;
 std::vector<double> lo, hi;
 size_t in=0, out=0;
 double last=-std::numeric_limits<double>::infinity();
 struct Crossing { int32_t row; double x; int32_t dir; };
 std::vector<Crossing> crossings, ordered;
 std::vector<size_t> row_start;
 bool vertex_less(size_t i,int a,int c) const {
  for(int k=0;k<3;++k){double va=value(i,a,k),vc=value(i,c,k);if(va!=vc)return va<vc;}
  return false;
 }
 double value(size_t i,int j,int k) const {
  auto p=static_cast<char*>(b.ptr)+i*b.strides[0]+j*b.strides[1]+k*b.strides[2];
  if(b.itemsize==4) {float x; std::memcpy(&x,p,4);return x;}
  double x; std::memcpy(&x,p,8);return x;
 }
public:
 Rasterizer(py::array triangles, py::object callback):data(triangles),b(data.request()) {
  if(b.ndim!=3 || b.shape[1]!=3 || b.shape[2]!=3 ||
   !(py::str(data.dtype().attr("kind")).cast<std::string>()=="f" && data.dtype().attr("isnative").cast<bool>() && (b.itemsize==4 || b.itemsize==8)))
   throw std::invalid_argument("triangles must be float32/float64 (n,3,3)");
  size_t n=b.shape[0];
  if(n>static_cast<size_t>(std::numeric_limits<int32_t>::max())) throw std::invalid_argument("too many triangles");
  lo.resize(n);hi.resize(n);entering.resize(n);leaving.resize(n);slot.assign(n,-1);
  py::gil_scoped_release release;
  for(size_t i=0;i<n;++i) {
   if((i&65535)==0 && !callback.is_none()) {py::gil_scoped_acquire g;callback();}
   lo[i]=std::numeric_limits<double>::infinity();hi[i]=-lo[i];
   for(int j=0;j<3;++j) for(int k=0;k<3;++k) {
    double v=value(i,j,k); if(!std::isfinite(v)) throw std::invalid_argument("nonfinite triangle");
    if(k==2){lo[i]=std::min(lo[i],v);hi[i]=std::max(hi[i],v);}
   }
  }
  std::iota(entering.begin(),entering.end(),0);std::iota(leaving.begin(),leaving.end(),0);
  std::sort(entering.begin(),entering.end(),[&](auto a,auto c){return lo[a]<lo[c] || (lo[a]==lo[c] && a<c);});
  std::sort(leaving.begin(),leaving.end(),[&](auto a,auto c){return hi[a]<hi[c] || (hi[a]==hi[c] && a<c);});
 }
 double lowest() const {return entering.empty()?std::numeric_limits<double>::quiet_NaN():lo[entering.front()];}
 double highest() const {return leaving.empty()?std::numeric_limits<double>::quiet_NaN():hi[leaving.back()];}
 void reset() {
  // Keep the sorted triangle index and allocated buffers for backward scrubs.
  // The owner must serialize reset/slice; both mutate the active sweep.
  for(auto id:active)slot[id]=-1;
  active.clear();in=0;out=0;last=-std::numeric_limits<double>::infinity();
 }
 py::dict slice(double z,int width,int height,double x0,double y0,double dx,double dy,py::object callback,const std::string &rule) {
  if(!std::isfinite(z)||z<last)throw std::invalid_argument("layers must be finite and nondecreasing");
  if(rule!="nonzero"&&rule!="evenodd")throw std::invalid_argument("rule must be nonzero or evenodd");
  if(width<1||height<1||static_cast<int64_t>(width)*height>200000000||!std::isfinite(dx)||!std::isfinite(dy)||dx<=0||dy<=0||!std::isfinite(x0)||!std::isfinite(y0))throw std::invalid_argument("invalid raster grid");
  last=z;
  const bool nonzero=(rule=="nonzero");
  py::array_t<uint8_t> mask({height,width});
  auto *pixels=mask.mutable_data();
  size_t odd=0,segments=0,filled=0,spans=0,negative=0;
  {
   py::gil_scoped_release release;
   std::memset(pixels,0,static_cast<size_t>(width)*height);
   while(in<entering.size()&&lo[entering[in]]<=z) {
    auto id=entering[in++];slot[id]=static_cast<int32_t>(active.size());active.push_back(id);
   }
   while(out<leaving.size()&&hi[leaving[out]]<=z) {
    auto id=leaving[out++];auto p=slot[id];
    if(p>=0){auto back=active.back();active[p]=back;slot[back]=p;active.pop_back();slot[id]=-1;}
   }
   crossings.clear();
   auto clamped_row=[&](double y) {return static_cast<int>(std::clamp(std::ceil((y-y0)/dy-0.5),0.0,static_cast<double>(height)));};
   for(size_t ai=0;ai<active.size();++ai) {
    if((ai&8191)==0&&!callback.is_none()){py::gil_scoped_acquire g;callback();}
    auto id=active[ai];
    // Half-open edge rule: a closed triangle yields exactly zero or two points.
    // The cross-section contour runs from the descending to the ascending edge
    // crossing, which orients it with solid material on its left.
    double down[2]{},up[2]{};int found=0;
    for(int a=0;a<3;++a){int c=(a+1)%3;double za=value(id,a,2),zc=value(id,c,2);
     bool rising=(za<=z&&z<zc),falling=(zc<=z&&z<za);
     if(!rising&&!falling)continue;
     // Interpolate along a canonically ordered edge. Two triangles sharing an
     // edge traverse it in opposite directions, and the two directions round to
     // different doubles. When that shared crossing lands on a sample row
     // center, the half-open row rule can drop the row from both segments and
     // punch a hole straight through solid material. Ordering the endpoints by
     // coordinate makes both triangles produce bit-identical crossings. This
     // relies on the shared vertices being welded; unwelded near-duplicates
     // still differ.
     int p=a,q=c;
     if(!vertex_less(id,p,q)){p=c;q=a;}
     double zp=value(id,p,2),zq=value(id,q,2);
     double t=(z-zp)/(zq-zp);
     double *target=rising?up:down;
     for(int k=0;k<2;++k)target[k]=value(id,p,k)+t*(value(id,q,k)-value(id,p,k));
     ++found;
    }
    if(found!=2)continue;
    ++segments;
    if(down[1]==up[1])continue;
    const bool ascending=up[1]>down[1];
    const double *a=ascending?down:up,*c=ascending?up:down;
    int first=clamped_row(a[1]),end=clamped_row(c[1]);
    if(crossings.size()+static_cast<size_t>(std::max(0,end-first))>20000000) throw std::runtime_error("single-layer intersection budget exceeded");
    for(int row=first;row<end;++row){double y=y0+(row+.5)*dy;
     // Accumulation runs left to right, so the sign is negated relative to a
     // rightward ray: a correctly oriented solid then reads winding +1 inside.
     crossings.push_back({row,a[0]+(y-a[1])*(c[0]-a[0])/(c[1]-a[1]),ascending?-1:1});
    }
   }
   // Counting sort by row keeps per-layer work linear in the crossing count.
   row_start.assign(static_cast<size_t>(height)+1,0);
   for(const auto &crossing:crossings)row_start[static_cast<size_t>(crossing.row)+1]++;
   for(int row=0;row<height;++row)row_start[row+1]+=row_start[row];
   ordered.resize(crossings.size());
   {
    std::vector<size_t> cursor(row_start.begin(),row_start.end()-1);
    for(const auto &crossing:crossings)ordered[cursor[static_cast<size_t>(crossing.row)]++]=crossing;
   }
   auto clamped_col=[&](double x) {return static_cast<int>(std::clamp(std::ceil((x-x0)/dx-0.5),0.0,static_cast<double>(width)));};
   for(int row=0;row<height;++row) {
    if((row&127)==0&&!callback.is_none()){py::gil_scoped_acquire g;callback();}
    auto begin=ordered.begin()+static_cast<std::ptrdiff_t>(row_start[row]);
    auto stop=ordered.begin()+static_cast<std::ptrdiff_t>(row_start[row+1]);
    if(begin==stop)continue;
    std::sort(begin,stop,[](const Crossing&l,const Crossing&r){return l.x<r.x;});
    int winding=0;double span_x=0;
    for(auto it=begin;it!=stop;++it) {
     const int before=winding;
     winding+=nonzero?it->dir:1;
     const bool was_inside=nonzero?(before!=0):((before&1)!=0),is_inside=nonzero?(winding!=0):((winding&1)!=0);
     if(!was_inside&&is_inside)span_x=it->x;
     else if(was_inside&&!is_inside) {
      int start=clamped_col(span_x),end=clamped_col(it->x);
      if(end>start){std::memset(pixels+static_cast<size_t>(row)*width+start,1,static_cast<size_t>(end-start));filled+=static_cast<size_t>(end-start);}
      ++spans;
     }
     if(winding<0)++negative;
    }
    // An unbalanced row means the cross-section contour is not closed here.
    // Report it; never extend the last span to the crop edge.
    if(nonzero?(winding!=0):((winding&1)!=0))++odd;
   }
  }
  py::dict result;
  result["mask"]=mask;result["odd_rows"]=odd;result["segments"]=segments;
  result["active_triangles"]=active.size();result["filled_pixels"]=filled;
  result["spans"]=spans;result["negative_winding_crossings"]=negative;
  result["rule"]=rule;result["crossings"]=crossings.size();
  return result;
 }
};
void bind_raster(py::module_& m) {
 py::class_<Rasterizer>(m,"Rasterizer")
 .def(py::init<py::array,py::object>(),py::arg("triangles"),py::arg("callback")=py::none())
 .def_property_readonly("lowest_z",&Rasterizer::lowest)
 .def_property_readonly("highest_z",&Rasterizer::highest)
 .def("reset",&Rasterizer::reset)
 .def("slice",&Rasterizer::slice,py::arg("z"),py::arg("width"),py::arg("height"),py::arg("x0"),py::arg("y0"),py::arg("dx"),py::arg("dy"),py::arg("callback")=py::none(),py::arg("rule")="nonzero");
}
