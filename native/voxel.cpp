// Occupancy volume, well-composedness repair and manifold boundary extraction.
//
// The repair only ever ADDS material, so it is monotone and terminates, and the
// added volume is exactly reportable. A digital set whose every 2x2x2 block has
// both a 6-connected occupied part and a 6-connected empty part is well-composed
// (Latecki), and the boundary of its voxel cubes is then a closed orientable
// 2-manifold with no nonmanifold edge or vertex and no self-intersection. That
// boundary is emitted directly, so no ambiguous marching-cubes case table is
// involved and the result is a valid Manifold solid by construction.
#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <unordered_map>
#include <vector>
namespace py = pybind11;
namespace {

// corner index = di + 2*dj + 4*dk within a 2x2x2 block
struct Tables {
 std::array<bool,256> good{};
 std::array<int8_t,256> fix{};
 Tables() {
  auto connected=[](unsigned cfg,bool occupied) {
   int members[8],n=0;
   for(int c=0;c<8;c++)if((((cfg>>c)&1)!=0)==occupied)members[n++]=c;
   if(n<2)return true;
   bool seen[8]={};int stack[8],top=0;stack[top++]=members[0];seen[members[0]]=true;int visited=1;
   while(top) {
    int c=stack[--top];
    for(int bit=1;bit<8;bit<<=1) {
     int other=c^bit;
     if(seen[other]||((((cfg>>other)&1)!=0)!=occupied))continue;
     seen[other]=true;stack[top++]=other;visited++;
    }
   }
   return visited==n;
  };
  for(unsigned cfg=0;cfg<256;cfg++)good[cfg]=connected(cfg,true)&&connected(cfg,false);
  for(unsigned cfg=0;cfg<256;cfg++) {
   fix[cfg]=-1;
   if(good[cfg])continue;
   for(int c=0;c<8;c++)if(!((cfg>>c)&1)&&good[cfg|(1u<<c)]){fix[cfg]=int8_t(c);break;}
   if(fix[cfg]<0)for(int c=0;c<8;c++)if(!((cfg>>c)&1)){fix[cfg]=int8_t(c);break;}
  }
 }
};
const Tables &tables(){static Tables t;return t;}

class VoxelVolume {
 int nx,ny,nz,sx,sy,sz;              // logical dims and padded strides
 double x0,y0,z0,dx,dy,dz;
 std::vector<uint8_t> cells;         // padded by one empty voxel on every side
 size_t at(int i,int j,int k) const {return size_t(k+1)*size_t(sx)*sy+size_t(j+1)*sx+size_t(i+1);}
public:
 VoxelVolume(int nx_,int ny_,int nz_,double x0_,double y0_,double z0_,double dx_,double dy_,double dz_)
  :nx(nx_),ny(ny_),nz(nz_),sx(nx_+2),sy(ny_+2),sz(nz_+2),x0(x0_),y0(y0_),z0(z0_),dx(dx_),dy(dy_),dz(dz_) {
  if(nx<1||ny<1||nz<1)throw std::invalid_argument("voxel dimensions must be positive");
  if(!(dx>0&&dy>0&&dz>0))throw std::invalid_argument("voxel pitch must be positive");
  size_t total=size_t(sx)*sy*sz;
  if(total>size_t(24)*1024*1024*1024)throw std::invalid_argument("voxel volume exceeds 24 GiB");
  cells.assign(total,0);
 }
 size_t voxel_count() const {return size_t(nx)*ny*nz;}
 void set_slice(int k,py::array_t<uint8_t,py::array::c_style|py::array::forcecast> mask) {
  if(k<0||k>=nz)throw std::invalid_argument("slice index outside volume");
  auto info=mask.request();
  if(info.ndim!=2||info.shape[0]!=ny||info.shape[1]!=nx)throw std::invalid_argument("mask shape must be (ny,nx)");
  const uint8_t *src=static_cast<const uint8_t*>(info.ptr);
  py::gil_scoped_release release;
  for(int j=0;j<ny;j++) {
   uint8_t *dst=cells.data()+at(0,j,k);
   const uint8_t *row=src+size_t(j)*nx;
   for(int i=0;i<nx;i++)dst[i]=row[i]?1:0;
  }
 }
 size_t occupied() const {
  size_t total=0;py::gil_scoped_release release;
  for(int k=0;k<nz;k++)for(int j=0;j<ny;j++){const uint8_t *row=cells.data()+at(0,j,k);for(int i=0;i<nx;i++)total+=row[i];}
  return total;
 }
 py::dict make_well_composed(int max_passes,py::object callback) {
  if(max_passes<1||max_passes>1024)throw std::invalid_argument("max_passes must be 1..1024");
  const auto &t=tables();
  size_t added=0,blocked=0;int passes=0;
  for(;passes<max_passes;passes++) {
   size_t changed=0;
   if(!callback.is_none())callback("well_composed",size_t(passes),size_t(max_passes));
   {
    py::gil_scoped_release release;
    for(int k=-1;k<nz;k++)for(int j=-1;j<ny;j++) {
     uint8_t *r00=cells.data()+at(-1,j,k),*r10=cells.data()+at(-1,j+1,k);
     uint8_t *r01=cells.data()+at(-1,j,k+1),*r11=cells.data()+at(-1,j+1,k+1);
     // Seed the odd (di=1) bits with the voxels left of the first block so the
     // shift below moves them into the di=0 positions on the first iteration.
     unsigned cfg=unsigned(r00[0])<<1|unsigned(r10[0])<<3|unsigned(r01[0])<<5|unsigned(r11[0])<<7;
     for(int i=-1;i<nx;i++) {
      const int n=i+1;
      cfg=(cfg&0b10101010)>>1|unsigned(r00[n+1])<<1|unsigned(r10[n+1])<<3|unsigned(r01[n+1])<<5|unsigned(r11[n+1])<<7;
      if(t.good[cfg])continue;
      const int corner=t.fix[cfg];
      const int ci=i+(corner&1),cj=j+((corner>>1)&1),ck=k+((corner>>2)&1);
      if(ci<0||cj<0||ck<0||ci>=nx||cj>=ny||ck>=nz){blocked++;continue;}
      cells[at(ci,cj,ck)]=1;changed++;added++;
      // Refresh the running configuration: the write may be inside this block.
      cfg=unsigned(r00[n])|unsigned(r00[n+1])<<1|unsigned(r10[n])<<2|unsigned(r10[n+1])<<3
        |unsigned(r01[n])<<4|unsigned(r01[n+1])<<5|unsigned(r11[n])<<6|unsigned(r11[n+1])<<7;
     }
    }
   }
   if(!changed)break;
  }
  py::dict out;
  out["added_voxels"]=added;out["passes"]=passes;out["boundary_blocked"]=blocked;
  out["converged"]=passes<max_passes;
  return out;
 }
 py::tuple extract_surface(py::object callback) {
  std::unordered_map<int64_t,uint32_t> index;
  std::vector<double> verts;std::vector<uint32_t> faces;
  const int64_t cy=int64_t(nx)+1,cz=(int64_t(nx)+1)*(int64_t(ny)+1);
  auto node=[&](int i,int j,int k)->uint32_t {
   int64_t key=int64_t(k)*cz+int64_t(j)*cy+int64_t(i);
   auto found=index.find(key);
   if(found!=index.end())return found->second;
   uint32_t id=uint32_t(verts.size()/3);
   if(verts.size()/3>=0xfffffffeu)throw std::runtime_error("surface exceeds 32-bit vertex capacity");
   verts.push_back(x0+i*dx);verts.push_back(y0+j*dy);verts.push_back(z0+k*dz);
   index.emplace(key,id);return id;
  };
  auto quad=[&](uint32_t a,uint32_t b,uint32_t c,uint32_t d) {
   faces.insert(faces.end(),{a,b,c,a,c,d});
  };
  for(int k=0;k<nz;k++) {
   if(!callback.is_none())callback("surface",size_t(k),size_t(nz));
   for(int j=0;j<ny;j++) {
    const uint8_t *row=cells.data()+at(0,j,k);
    for(int i=0;i<nx;i++) {
     if(!row[i])continue;
     if(!cells[at(i+1,j,k)])quad(node(i+1,j,k),node(i+1,j+1,k),node(i+1,j+1,k+1),node(i+1,j,k+1));
     if(!cells[at(i-1,j,k)])quad(node(i,j,k),node(i,j,k+1),node(i,j+1,k+1),node(i,j+1,k));
     if(!cells[at(i,j+1,k)])quad(node(i,j+1,k),node(i,j+1,k+1),node(i+1,j+1,k+1),node(i+1,j+1,k));
     if(!cells[at(i,j-1,k)])quad(node(i,j,k),node(i+1,j,k),node(i+1,j,k+1),node(i,j,k+1));
     if(!cells[at(i,j,k+1)])quad(node(i,j,k+1),node(i+1,j,k+1),node(i+1,j+1,k+1),node(i,j+1,k+1));
     if(!cells[at(i,j,k-1)])quad(node(i,j,k),node(i,j+1,k),node(i+1,j+1,k),node(i+1,j,k));
    }
   }
  }
  py::array_t<double> vertices({py::ssize_t(verts.size()/3),py::ssize_t(3)});
  std::memcpy(vertices.mutable_data(),verts.data(),verts.size()*sizeof(double));
  py::array_t<uint32_t> triangles({py::ssize_t(faces.size()/3),py::ssize_t(3)});
  std::memcpy(triangles.mutable_data(),faces.data(),faces.size()*sizeof(uint32_t));
  return py::make_tuple(vertices,triangles);
 }
};
}
void bind_voxel(py::module_ &m) {
 py::class_<VoxelVolume>(m,"VoxelVolume")
  .def(py::init<int,int,int,double,double,double,double,double,double>(),
   py::arg("nx"),py::arg("ny"),py::arg("nz"),py::arg("x0"),py::arg("y0"),py::arg("z0"),
   py::arg("dx"),py::arg("dy"),py::arg("dz"))
  .def("set_slice",&VoxelVolume::set_slice,py::arg("index"),py::arg("mask"))
  .def("occupied",&VoxelVolume::occupied)
  .def_property_readonly("voxel_count",&VoxelVolume::voxel_count)
  .def("make_well_composed",&VoxelVolume::make_well_composed,py::arg("max_passes")=64,py::arg("callback")=py::none())
  .def("extract_surface",&VoxelVolume::extract_surface,py::arg("callback")=py::none());
}
