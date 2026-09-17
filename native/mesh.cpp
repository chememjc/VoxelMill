// Topology kernels. Exact-coordinate welding by default; optional bounded
// tolerance weld via spatial hash. No geometry deletion beyond invalidation.
#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <unordered_map>
#include <vector>
namespace py=pybind11;
namespace {
struct Vertex { double x,y,z; uint32_t corner; };
struct Edge { uint32_t a,b,face; bool forward; };
struct DSU {
 std::vector<uint32_t> p; std::vector<uint8_t> rank;
 explicit DSU(size_t n):p(n),rank(n,0){std::iota(p.begin(),p.end(),0);}
 uint32_t find(uint32_t a){while(p[a]!=a){p[a]=p[p[a]];a=p[a];}return a;}
 void join(uint32_t a,uint32_t b){a=find(a);b=find(b);if(a==b)return;if(rank[a]<rank[b])std::swap(a,b);p[b]=a;if(rank[a]==rank[b])rank[a]++;}
};
struct Input {
 py::buffer_info b;
 size_t n;
 explicit Input(const py::array &a):b(a.request()) {
  if(b.ndim!=3 || b.shape[1]!=3 || b.shape[2]!=3 || (a.dtype().kind()!='f' || (b.itemsize!=4 && b.itemsize!=8) || !a.dtype().attr("isnative").cast<bool>()))
   throw std::invalid_argument("triangles must be a native-endian float32 or float64 array of shape (n,3,3)");
  n=b.shape[0];if(n>std::numeric_limits<uint32_t>::max()/3)throw std::invalid_argument("triangle count exceeds kernel index capacity");
 }
 double at(size_t t,int v,int c)const{auto ptr=static_cast<char*>(b.ptr)+t*b.strides[0]+v*b.strides[1]+c*b.strides[2];if(b.itemsize==4){float value;std::memcpy(&value,ptr,4);return value;}double value;std::memcpy(&value,ptr,8);return value;}
};
void tick(const py::object &cb,const char *stage,size_t done,size_t total){if(!cb.is_none()){py::gil_scoped_acquire lock;cb(stage,done,total);}}
constexpr double kMaxWeldToleranceMm=0.05;
void check_weld_tolerance(double tolerance){
 if(!std::isfinite(tolerance)||tolerance<0)throw std::invalid_argument("weld tolerance must be a finite non-negative number");
 if(tolerance>kMaxWeldToleranceMm)throw std::invalid_argument("weld tolerance must be <= 0.05 mm");
}
struct Cell { int64_t x,y,z; bool operator==(const Cell &o)const{return x==o.x&&y==o.y&&z==o.z;} };
struct CellHash {
 size_t operator()(const Cell &c)const{
  size_t h=std::hash<int64_t>{}(c.x);
  h^=std::hash<int64_t>{}(c.y)+0x9e3779b97f4a7c15ULL+(h<<6)+(h>>2);
  h^=std::hash<int64_t>{}(c.z)+0x9e3779b97f4a7c15ULL+(h<<6)+(h>>2);
  return h;
 }
};
struct Weld {std::vector<Vertex> vertices; std::vector<uint32_t> faces; std::vector<uint8_t> invalid; size_t nonfinite=0,degenerate=0;std::array<double,3> lo,hi; long double volume=0;};
Weld weld(const Input &a,const py::object &cb,double tolerance=0){
 check_weld_tolerance(tolerance);
 Weld w; w.lo.fill(std::numeric_limits<double>::infinity());w.hi.fill(-std::numeric_limits<double>::infinity());
 w.vertices.resize(a.n*3);w.faces.resize(a.n*3);w.invalid.resize(a.n,0);
 for(size_t i=0;i<a.n;i++){
  if(i%65536==0)tick(cb,"vertices",i,a.n);
  double p[3][3];bool finite=true;
  for(int j=0;j<3;j++){for(int k=0;k<3;k++){p[j][k]=a.at(i,j,k);finite=finite&&std::isfinite(p[j][k]);if(std::isfinite(p[j][k])){w.lo[k]=std::min(w.lo[k],p[j][k]);w.hi[k]=std::max(w.hi[k],p[j][k]);}}}
  double u[3],v[3],cross[3];for(int k=0;k<3;k++){u[k]=p[1][k]-p[0][k];v[k]=p[2][k]-p[0][k];}
  cross[0]=u[1]*v[2]-u[2]*v[1];cross[1]=u[2]*v[0]-u[0]*v[2];cross[2]=u[0]*v[1]-u[1]*v[0];
  if(!finite){w.nonfinite++;w.invalid[i]=1;}
  else if(cross[0]==0&&cross[1]==0&&cross[2]==0){w.degenerate++;w.invalid[i]=1;}
  if(finite){w.volume+=(static_cast<long double>(p[0][0])*(p[1][1]*p[2][2]-p[1][2]*p[2][1])+static_cast<long double>(p[0][1])*(p[1][2]*p[2][0]-p[1][0]*p[2][2])+static_cast<long double>(p[0][2])*(p[1][0]*p[2][1]-p[1][1]*p[2][0]))/6;}
  for(int j=0;j<3;j++)w.vertices[3*i+j]={finite?p[j][0]:0.,finite?p[j][1]:0.,finite?p[j][2]:0.,uint32_t(3*i+j)};
 }
 if(tolerance==0){
  tick(cb,"weld_sort",0,w.vertices.size());
  std::sort(w.vertices.begin(),w.vertices.end(),[](const Vertex&a,const Vertex&b){if(a.x!=b.x)return a.x<b.x;if(a.y!=b.y)return a.y<b.y;return a.z<b.z;});
  size_t unique=0;
  for(size_t i=0;i<w.vertices.size();i++){
   if(i%262144==0)tick(cb,"weld",i,w.vertices.size());
   Vertex current=w.vertices[i];
   if(!unique || current.x!=w.vertices[unique-1].x || current.y!=w.vertices[unique-1].y || current.z!=w.vertices[unique-1].z)w.vertices[unique++]=current;
   w.faces[current.corner]=uint32_t(unique-1);
  }
  w.vertices.resize(unique);return w;
 }
 // Bounded tolerant weld: grid cell size = tolerance; search the 3x3x3 neighbourhood.
 const double tol2=tolerance*tolerance;
 std::unordered_map<Cell,std::vector<uint32_t>,CellHash> grid;
 std::vector<Vertex> unique;unique.reserve(w.vertices.size());
 tick(cb,"weld",0,w.vertices.size());
 for(size_t i=0;i<w.vertices.size();i++){
  if(i%262144==0)tick(cb,"weld",i,w.vertices.size());
  const Vertex current=w.vertices[i];
  const Cell cell{static_cast<int64_t>(std::floor(current.x/tolerance)),
                  static_cast<int64_t>(std::floor(current.y/tolerance)),
                  static_cast<int64_t>(std::floor(current.z/tolerance))};
  uint32_t found=std::numeric_limits<uint32_t>::max();
  for(int dx=-1;dx<=1&&found==std::numeric_limits<uint32_t>::max();++dx)
  for(int dy=-1;dy<=1&&found==std::numeric_limits<uint32_t>::max();++dy)
  for(int dz=-1;dz<=1&&found==std::numeric_limits<uint32_t>::max();++dz){
   auto it=grid.find(Cell{cell.x+dx,cell.y+dy,cell.z+dz});
   if(it==grid.end())continue;
   for(uint32_t id:it->second){
    const Vertex &rep=unique[id];
    const double ex=current.x-rep.x,ey=current.y-rep.y,ez=current.z-rep.z;
    if(ex*ex+ey*ey+ez*ez<=tol2){found=id;break;}
   }
  }
  if(found==std::numeric_limits<uint32_t>::max()){
   found=uint32_t(unique.size());
   unique.push_back(current);
   grid[cell].push_back(found);
  }
  w.faces[current.corner]=found;
 }
 w.vertices=std::move(unique);
 for(size_t i=0;i<a.n;i++){
  if(w.invalid[i])continue;
  const uint32_t a0=w.faces[3*i],a1=w.faces[3*i+1],a2=w.faces[3*i+2];
  if(a0==a1||a1==a2||a0==a2){w.degenerate++;w.invalid[i]=1;}
 }
 return w;
}
py::dict inspect(const py::array &arr,const py::object&cb){
 Input input(arr); Weld w;size_t boundary=0,nonmanifold=0,winding=0,components=0,edges_n=0;std::vector<size_t> sizes;
 {py::gil_scoped_release release;w=weld(input,cb);std::vector<Edge> edges;edges.reserve(input.n*3);DSU dsu(input.n);
  for(size_t i=0;i<input.n;i++)if(!w.invalid[i]){for(int j=0;j<3;j++){uint32_t a=w.faces[3*i+j],b=w.faces[3*i+(j+1)%3];edges.push_back({std::min(a,b),std::max(a,b),uint32_t(i),a<b});}}
  tick(cb,"edges_sort",0,edges.size());std::sort(edges.begin(),edges.end(),[](const Edge&a,const Edge&b){return a.a<b.a||(a.a==b.a&&a.b<b.b);});
  for(size_t i=0;i<edges.size();){if(i%262144<4)tick(cb,"edges",i,edges.size());size_t j=i+1;while(j<edges.size()&&edges[j].a==edges[i].a&&edges[j].b==edges[i].b){dsu.join(edges[i].face,edges[j].face);j++;}edges_n++;if(j-i==1)boundary++;else if(j-i>2)nonmanifold++;else if(edges[i].forward==edges[i+1].forward)winding++;i=j;}
  std::vector<size_t> counts(input.n,0);for(size_t i=0;i<input.n;i++)if(!w.invalid[i])counts[dsu.find(i)]++;
  for(auto count:counts)if(count){components++;sizes.push_back(count);}std::sort(sizes.begin(),sizes.end(),std::greater<size_t>());
  tick(cb,"complete",input.n,input.n);
 }
 py::dict result;result["triangle_count"]=input.n;result["unique_vertices"]=w.vertices.size();result["valid_triangle_count"]=input.n-w.nonfinite-w.degenerate;result["nonfinite_triangles"]=w.nonfinite;result["degenerate_triangles"]=w.degenerate;result["invalid_triangles"]=w.nonfinite+w.degenerate;result["boundary_edges"]=boundary;result["nonmanifold_edges"]=nonmanifold;result["inconsistent_winding_edges"]=winding;result["unique_edges"]=edges_n;result["connected_components"]=components;
 py::list component_sizes;for(auto n:sizes)component_sizes.append(n);result["component_triangle_counts"]=component_sizes;
 py::list bounds;for(auto side:{w.lo,w.hi}){py::list row;for(double x:side){if(std::isfinite(x))row.append(x);else row.append(py::none());}bounds.append(row);}result["bounds"]=bounds;result["signed_volume_mm3"]=double(w.volume);result["self_intersections_status"]="not_run";result["self_intersections"]=py::none();result["topology_connectivity"]="shared exact-coordinate edge; invalid triangles excluded and reported";
 return result;
}
py::tuple welded(const py::array &arr,const py::object&cb,double weld_tolerance_mm=0){
 Input input(arr); Weld w;
 {py::gil_scoped_release release;w=weld(input,cb,weld_tolerance_mm);}
 if(w.nonfinite||w.degenerate)throw std::invalid_argument("Cannot weld invalid triangles without an explicit repair policy");
 py::array_t<double> v({py::ssize_t(w.vertices.size()),py::ssize_t(3)});
 py::array_t<uint32_t> f({py::ssize_t(input.n),py::ssize_t(3)});
 auto vp=v.mutable_unchecked<2>();
 for(size_t i=0;i<w.vertices.size();i++){vp(i,0)=w.vertices[i].x;vp(i,1)=w.vertices[i].y;vp(i,2)=w.vertices[i].z;}
 std::memcpy(f.mutable_data(),w.faces.data(),w.faces.size()*4);
 return py::make_tuple(v,f);
}
}
void bind_mesh(py::module_ &m){
 m.def("inspect_mesh",&inspect,py::arg("triangles"),py::arg("callback")=py::none());
 m.def("weld_mesh",&welded,py::arg("triangles"),py::arg("callback")=py::none(),py::arg("weld_tolerance_mm")=0.0);
}
