#include <cuda_runtime.h>
#include <cstdint>
__global__ void morph(const uint8_t*s,uint8_t*t,int w,int h,int rx,int ry,bool erode){
 int x=blockIdx.x*blockDim.x+threadIdx.x,y=blockIdx.y*blockDim.y+threadIdx.y;if(x>=w||y>=h)return;bool r=erode;
 for(int oy=-ry;oy<=ry;++oy)for(int ox=-rx;ox<=rx;++ox){double nx=rx?(double)ox/rx:(ox?2.:0.),ny=ry?(double)oy/ry:(oy?2.:0.);
  if(nx*nx+ny*ny>1.)continue;bool on=x+ox>=0&&x+ox<w&&y+oy>=0&&y+oy<h&&s[(y+oy)*w+x+ox];
  if(erode&&!on){r=false;goto done;}if(!erode&&on){r=true;goto done;}}done:t[y*w+x]=r?1:0;}
extern "C" const char*voxelmill_cuda_status(int*c){cudaError_t e=cudaGetDeviceCount(c);return e==cudaSuccess?nullptr:cudaGetErrorString(e);}
extern "C" const char*voxelmill_cuda_morphology(const uint8_t*i,uint8_t*o,int w,int h,int rx,int ry,bool erode,int dev){
 cudaError_t e=cudaSetDevice(dev);if(e!=cudaSuccess)return cudaGetErrorString(e);size_t n=(size_t)w*h;uint8_t*s=0,*t=0;
 e=cudaMalloc(&s,n);if(e!=cudaSuccess)return cudaGetErrorString(e);e=cudaMalloc(&t,n);if(e!=cudaSuccess){cudaFree(s);return cudaGetErrorString(e);}
 e=cudaMemcpy(s,i,n,cudaMemcpyHostToDevice);if(e==cudaSuccess){dim3 b(16,16),g((w+15)/16,(h+15)/16);morph<<<g,b>>>(s,t,w,h,rx,ry,erode);e=cudaGetLastError();}
 if(e==cudaSuccess)e=cudaMemcpy(o,t,n,cudaMemcpyDeviceToHost);cudaFree(s);cudaFree(t);return e==cudaSuccess?nullptr:cudaGetErrorString(e);}
