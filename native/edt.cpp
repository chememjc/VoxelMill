// 3D Euclidean distance transform. Separable 1-D lower envelope of parabolas
// (Maurer 2003 / Felzenszwalb–Huttenlocher), specialised to a C-contiguous
// (z, y, x) volume. Distances are recovered from integer feature coordinates
// with scipy's formula so the float64 field matches
// `ndi.distance_transform_edt` bit for bit, including tie-breaks.
//
// scipy writes a feature-index volume then does, in Python:
//   dt = (ft - indices).astype(float64)
//   dt[i] *= sampling[i]
//   square; add.reduce along the index axis; sqrt.
// The no-background case is a scipy quirk: every voxel is measured to (-1, 0, 0).
#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <vector>
#ifdef VOXELMILL_TBB
#include <tbb/parallel_for.h>
#include <tbb/blocked_range.h>
#endif
namespace py = pybind11;
namespace {

template<class F>
void parallel_n(int n, F &&fn) {
#ifdef VOXELMILL_TBB
 tbb::parallel_for(tbb::blocked_range<int>(0, n), [&](const tbb::blocked_range<int> &r) {
  for (int i = r.begin(); i < r.end(); ++i) fn(i);
 });
#else
 for (int i = 0; i < n; ++i) fn(i);
#endif
}

struct Feat { int32_t c[3]; };

inline size_t at(int z, int y, int x, int ny, int nx) {
 return (size_t(z) * size_t(ny) + size_t(y)) * size_t(nx) + size_t(x);
}

struct Scratch {
 std::vector<Feat> line, orig;
 std::vector<int> g;
 explicit Scratch(int n): line(n), orig(n), g(n) {}
};

// scipy's `_VoronoiFT` on one line. `line` is both input (copied features of
// each site) and output (the winning feature for that site).
void voronoi_ft(Feat *line, int len, int d, const int coor[3], const double samp[3],
                Scratch &s) {
 int *g = s.g.data();
 int l = -1;
 for (int ii = 0; ii < len; ++ii) {
  if (line[ii].c[0] < 0) continue;
  const double fd = double(line[ii].c[d]);
  double wR = 0.0;
  for (int jj = 0; jj < 3; ++jj) {
   if (jj == d) continue;
   double tw = double(line[ii].c[jj] - coor[jj]);
   tw *= samp[jj];
   wR += tw * tw;
  }
  while (l >= 1) {
   const int idx1 = g[l];
   const double f1 = double(line[idx1].c[d]);
   const int idx2 = g[l - 1];
   double a = f1 - double(line[idx2].c[d]);
   double b = fd - f1;
   a *= samp[d];
   b *= samp[d];
   const double c = a + b;
   double uR = 0.0, vR = 0.0;
   for (int jj = 0; jj < 3; ++jj) {
    if (jj == d) continue;
    const double cc = double(coor[jj]);
    double tu = double(line[idx2].c[jj]) - cc;
    double tv = double(line[idx1].c[jj]) - cc;
    tu *= samp[jj];
    tv *= samp[jj];
    uR += tu * tu;
    vR += tv * tv;
   }
   if (c * vR - b * uR - a * wR - a * b * c <= 0.0) break;
   --l;
  }
  ++l;
  g[l] = ii;
 }
 const int maxl = l;
 if (maxl < 0) return;
 std::memcpy(s.orig.data(), line, size_t(len) * sizeof(Feat));
 const Feat *orig = s.orig.data();
 l = 0;
 for (int ii = 0; ii < len; ++ii) {
  double delta1 = 0.0;
  for (int jj = 0; jj < 3; ++jj) {
   double t = jj == d ? double(orig[g[l]].c[jj] - ii) : double(orig[g[l]].c[jj] - coor[jj]);
   t *= samp[jj];
   delta1 += t * t;
  }
  while (l < maxl) {
   double delta2 = 0.0;
   for (int jj = 0; jj < 3; ++jj) {
    double t = jj == d ? double(orig[g[l + 1]].c[jj] - ii)
                       : double(orig[g[l + 1]].c[jj] - coor[jj]);
    t *= samp[jj];
    delta2 += t * t;
   }
   if (delta1 <= delta2) break;
   delta1 = delta2;
   ++l;
  }
  line[ii] = orig[g[l]];
 }
}

inline double scipy_dist(int z, int y, int x, Feat f, const double samp[3]) {
 const double az = double(int32_t(z) - f.c[0]) * samp[0];
 const double ay = double(int32_t(y) - f.c[1]) * samp[1];
 const double ax = double(int32_t(x) - f.c[2]) * samp[2];
 return std::sqrt((az * az + ay * ay) + ax * ax);
}

py::array_t<double> distance_transform_edt(const py::array &mask, py::object sampling) {
 auto info = mask.request();
 if (info.ndim != 3) throw std::invalid_argument("mask must be three dimensional");
 const char kind = mask.dtype().kind();
 if (info.itemsize != 1 || (kind != 'b' && kind != 'u' && kind != 'i'))
  throw std::invalid_argument("mask must be a one-byte boolean or integer dtype");
 const int nz = int(info.shape[0]), ny = int(info.shape[1]), nx = int(info.shape[2]);
 if (nz < 1 || ny < 1 || nx < 1) throw std::invalid_argument("mask dimensions must be positive");
 if (info.strides[2] != 1 || info.strides[1] != info.shape[2] ||
     info.strides[0] != info.shape[2] * info.shape[1])
  throw std::invalid_argument("mask must be C-contiguous");
 double samp[3] = {1.0, 1.0, 1.0};
 if (!sampling.is_none()) {
  py::sequence seq(sampling);
  const py::ssize_t n = seq.size();
  if (n == 1) {
   samp[0] = samp[1] = samp[2] = seq[0].cast<double>();
  } else if (n == 3) {
   samp[0] = seq[0].cast<double>(); samp[1] = seq[1].cast<double>(); samp[2] = seq[2].cast<double>();
  } else {
   throw std::invalid_argument("sampling must have 1 or 3 entries");
  }
  if (!(samp[0] > 0 && samp[1] > 0 && samp[2] > 0) ||
      !std::isfinite(samp[0]) || !std::isfinite(samp[1]) || !std::isfinite(samp[2]))
   throw std::invalid_argument("sampling must be finite and positive");
 }
 py::array_t<double> out({nz, ny, nx});
 double *dist = out.mutable_data();
 const uint8_t *src = static_cast<const uint8_t *>(info.ptr);
 const size_t n = size_t(nz) * size_t(ny) * size_t(nx);
 std::vector<int32_t> ftz(n, -1), fty(n, 0), ftx(n, 0);
 {
  py::gil_scoped_release release;
  // Axis 0 (z): seed background features, then 1-D Voronoi. Matches scipy's
  // innermost `_ComputeFT(d=0)` so the no-background leftover is (-1, 0, 0).
  parallel_n(ny, [&](int y) {
   Scratch s(nz);
   for (int x = 0; x < nx; ++x) {
    int coor[3] = {0, y, x};
    for (int z = 0; z < nz; ++z) {
     const size_t i = at(z, y, x, ny, nx);
     if (src[i]) s.line[z] = Feat{{-1, 0, 0}};
     else s.line[z] = Feat{{z, y, x}};
    }
    voronoi_ft(s.line.data(), nz, 0, coor, samp, s);
    for (int z = 0; z < nz; ++z) {
     const size_t i = at(z, y, x, ny, nx);
     ftz[i] = s.line[z].c[0]; fty[i] = s.line[z].c[1]; ftx[i] = s.line[z].c[2];
    }
   }
  });
  // Axis 1 (y).
  parallel_n(nz, [&](int z) {
   Scratch s(ny);
   for (int x = 0; x < nx; ++x) {
    int coor[3] = {z, 0, x};
    for (int y = 0; y < ny; ++y) {
     const size_t i = at(z, y, x, ny, nx);
     s.line[y] = Feat{{ftz[i], fty[i], ftx[i]}};
    }
    voronoi_ft(s.line.data(), ny, 1, coor, samp, s);
    for (int y = 0; y < ny; ++y) {
     const size_t i = at(z, y, x, ny, nx);
     ftz[i] = s.line[y].c[0]; fty[i] = s.line[y].c[1]; ftx[i] = s.line[y].c[2];
    }
   }
  });
  // Axis 2 (x), contiguous.
  parallel_n(nz, [&](int z) {
   Scratch s(nx);
   for (int y = 0; y < ny; ++y) {
    int coor[3] = {z, y, 0};
    const size_t row = at(z, y, 0, ny, nx);
    for (int x = 0; x < nx; ++x)
     s.line[x] = Feat{{ftz[row + size_t(x)], fty[row + size_t(x)], ftx[row + size_t(x)]}};
    voronoi_ft(s.line.data(), nx, 2, coor, samp, s);
    for (int x = 0; x < nx; ++x)
     dist[row + size_t(x)] = scipy_dist(z, y, x, s.line[x], samp);
   }
  });
 }
 return out;
}

}

void bind_edt(py::module_ &m) {
 m.def("distance_transform_edt", &distance_transform_edt, py::arg("mask"),
       py::arg("sampling") = py::none(),
       "3D Euclidean distance transform; float64 distances match scipy.ndimage.distance_transform_edt");
}
