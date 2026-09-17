// Run-length (RLE) layer kernels: exact run-space equivalents of the per-layer
// analysis primitives in src/voxelmill/validation.py.
//
// A binary row-RLE stores each row as the intervals where the mask is set. The
// material runs and the void runs of a row are the same partition read with
// opposite polarity -- a row with k transitions has k+1 runs whichever colour
// is called foreground -- so one extraction serves both the island path and the
// void path. Measured on the real bracket build: 8,640 runs against 3,542,226
// pixels per layer, a 410x compression that holds for every fixture and under
// antialiasing (validation only ever sees `mask != 0`, so AA fringe changes run
// *lengths*, never run counts).
//
// Exactness, not speed, is the contract here. Every kernel reproduces its dense
// NumPy/scipy counterpart bit for bit, including `scipy.ndimage.label`'s
// component numbering and the emission order of `VoidForest.merge`'s union
// calls. Two consequences are load-bearing and are called out where they live:
//
//   * `run_ccl` relabels in row-major first-encounter order, which is how
//     `ndi.label` numbers components. Any other order shifts every component id
//     and with it the diagnostic order and `overlap[component]`.
//   * `run_pairs` keeps the 64-row chunking of validation.py:185-189 verbatim,
//     duplicates and all. `VoidForest.union` accumulates volumes in floating
//     point, which is not associative, so the call sequence is part of the
//     reported number (gotchas.md; tests/test_validation.py::
//     test_merge_union_sequence_matches_reference).
//
// Runs are half-open [start, end) column intervals, sorted and disjoint within
// a row. `row_offsets` has height + 1 entries: the runs of row r are
// [row_offsets[r], row_offsets[r + 1]).
#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <vector>

namespace py = pybind11;
namespace {

using I32 = py::array_t<int32_t, py::array::c_style>;

// Largest run count `extract_runs` will materialise for one layer, mirroring
// the `MERGE_KEY_TABLE_CAP` convention at validation.py:36-41. 16 Mi runs is
// 128 MiB of start/end scratch; a layer can only reach it by alternating every
// other pixel over a panel larger than the 200 Mpx the rasterizer allows, so on
// any real panel this is an absurd-input guard rather than a working limit
// (worst case for the 1409x2514 bracket panel is 1,772,522 runs). Above it the
// kernels refuse and the caller pays for the dense path, whose int32 label
// array costs 4*h*w against the runs' 8*n -- the two cross at exactly the
// theoretical maximum run count, so run space never costs more memory.
constexpr int64_t RUN_TABLE_CAP = 1 << 24;

// Mean pixels per run below which run space stops paying. Measured on Bernoulli
// noise over a 1409x2514 panel, extraction + run-CCL against `ndi.label`:
// 4.0 px/run 1.48x, 6.2 px/run 0.94x (loses), 21.1 px/run 1.58x, 102 px/run
// 1.83x, 500 px/run 3.24x. Every measured fixture sits at 235-1,970 px/run, two
// orders clear. The Python side compares `panel_pixels / max(1, run_count)`
// against this and takes the dense path below it; the run count is simply
// `len(starts)`, so the decision costs nothing beyond the extraction already
// paid (0.52 ms against the 39 ms dense chain it replaces).
constexpr int64_t RUN_DENSITY_FLOOR = 6;

struct Runs {
 const int32_t *s, *e, *row;
 int64_t n, h;
};

// Validate and open a run set. O(n + h) over arrays two to three orders smaller
// than the panel, so the checks are free relative to the work they protect.
Runs open_runs(const I32 &starts, const I32 &ends, const I32 &row_offsets) {
 if (starts.ndim() != 1 || ends.ndim() != 1 || row_offsets.ndim() != 1)
  throw std::invalid_argument("run arrays must be one dimensional");
 if (starts.shape(0) != ends.shape(0))
  throw std::invalid_argument("run starts and ends differ in length");
 if (row_offsets.shape(0) < 1)
  throw std::invalid_argument("row offsets must hold height + 1 entries");
 Runs r{starts.data(), ends.data(), row_offsets.data(), int64_t(starts.shape(0)),
        int64_t(row_offsets.shape(0)) - 1};
 if (r.n > RUN_TABLE_CAP) throw std::invalid_argument("run count exceeds the run table cap");
 if (r.row[0] != 0 || int64_t(r.row[r.h]) != r.n)
  throw std::invalid_argument("row offsets must run from zero to the run count");
 for (int64_t i = 0; i < r.h; ++i) {
  const int32_t a = r.row[i], b = r.row[i + 1];
  if (a > b || b > r.n) throw std::invalid_argument("row offsets must be nondecreasing");
  for (int32_t k = a; k < b; ++k) {
   if (r.s[k] < 0 || r.e[k] <= r.s[k]) throw std::invalid_argument("runs must be nonempty and nonnegative");
   if (k > a && r.s[k] < r.e[k - 1]) throw std::invalid_argument("runs must be sorted and disjoint within a row");
  }
 }
 return r;
}

const int32_t *open_labels(const I32 &labels, int64_t n, int32_t count) {
 if (labels.ndim() != 1 || int64_t(labels.shape(0)) != n)
  throw std::invalid_argument("one label per run is required");
 if (count < 0) throw std::invalid_argument("component count must be nonnegative");
 const int32_t *l = labels.data();
 for (int64_t i = 0; i < n; ++i)
  if (l[i] < 1 || l[i] > count) throw std::invalid_argument("run labels must lie in 1..count");
 return l;
}

void same_height(const Runs &a, const Runs &b) {
 if (a.h != b.h) throw std::invalid_argument("run sets describe different panel heights");
}

I32 to_array(const std::vector<int32_t> &v) {
 I32 out(py::ssize_t(v.size()));
 if (!v.empty()) std::memcpy(out.mutable_data(), v.data(), v.size() * sizeof(int32_t));
 return out;
}

// ---- extraction ---------------------------------------------------------

// Eight bytes at a time while the boolean value does not change. The zero test
// is the classic word-wise haszero; it is exact, not an approximation, so the
// skip never changes a run boundary.
inline bool uniform8(const uint8_t *p, bool value) {
 uint64_t v;
 std::memcpy(&v, p, 8);
 if (!value) return v == 0;
 return ((v - 0x0101010101010101ull) & ~v & 0x8080808080808080ull) == 0;
}

py::tuple extract_runs(const py::array &mask, int want, int64_t cap) {
 if (want != 0 && want != 1) throw std::invalid_argument("want must be 0 or 1");
 if (cap < 1 || cap > RUN_TABLE_CAP) throw std::invalid_argument("cap must lie in 1..RUN_TABLE_CAP");
 auto info = mask.request();
 if (info.ndim != 2) throw std::invalid_argument("mask must be two dimensional");
 const char kind = mask.dtype().kind();
 if (info.itemsize != 1 || (kind != 'b' && kind != 'u' && kind != 'i'))
  throw std::invalid_argument("mask must be a one-byte boolean or integer dtype");
 const int64_t h = info.shape[0], w = info.shape[1];
 if (h && w && (info.strides[1] != 1 || info.strides[0] != w))
  throw std::invalid_argument("mask must be C-contiguous");
 if (w > std::numeric_limits<int32_t>::max()) throw std::invalid_argument("panel is too wide");
 const bool target = want != 0;
 std::vector<int32_t> s, e, row(size_t(h) + 1, 0);
 {
  py::gil_scoped_release release;
  const uint8_t *base = static_cast<const uint8_t *>(info.ptr);
  s.reserve(4096);
  e.reserve(4096);
  for (int64_t r = 0; r < h; ++r) {
   row[size_t(r)] = int32_t(s.size());
   const uint8_t *p = base + size_t(r) * size_t(w);
   int64_t c = 0;
   while (c < w) {
    while (c < w) {
     if (c + 8 <= w && uniform8(p + c, !target)) { c += 8; continue; }
     if ((p[c] != 0) == target) break;
     ++c;
    }
    if (c >= w) break;
    const int64_t begin = c;
    while (c < w) {
     if (c + 8 <= w && uniform8(p + c, target)) { c += 8; continue; }
     if ((p[c] != 0) != target) break;
     ++c;
    }
    if (int64_t(s.size()) >= cap)
     throw std::invalid_argument("layer exceeds the run cap; use the dense path");
    s.push_back(int32_t(begin));
    e.push_back(int32_t(c));
   }
  }
  row[size_t(h)] = int32_t(s.size());
 }
 return py::make_tuple(to_array(s), to_array(e), to_array(row));
}

// The complement partition of the same transitions, in O(runs) rather than a
// second O(pixels) read of the panel: material runs and void runs are the same
// row partition, so one `extract_runs` serves both halves of the validation
// chain.
py::tuple complement_runs(const I32 &starts, const I32 &ends, const I32 &row_offsets, int64_t width) {
 Runs a = open_runs(starts, ends, row_offsets);
 if (width < 0 || width > std::numeric_limits<int32_t>::max())
  throw std::invalid_argument("width must be a nonnegative int32");
 for (int64_t i = 0; i < a.n; ++i)
  if (a.e[i] > width) throw std::invalid_argument("run extends past the panel width");
 std::vector<int32_t> s, e, row(size_t(a.h) + 1, 0);
 {
  py::gil_scoped_release release;
  for (int64_t r = 0; r < a.h; ++r) {
   row[size_t(r)] = int32_t(s.size());
   int32_t cursor = 0;
   for (int32_t i = a.row[r]; i < a.row[r + 1]; ++i) {
    if (a.s[i] > cursor) { s.push_back(cursor); e.push_back(a.s[i]); }
    cursor = a.e[i];
   }
   if (cursor < width) { s.push_back(cursor); e.push_back(int32_t(width)); }
  }
  row[size_t(a.h)] = int32_t(s.size());
 }
 return py::make_tuple(to_array(s), to_array(e), to_array(row));
}

// Runs of (a & ~b), the run-space `mask & ~previous` the growth check needs.
py::tuple run_difference(const I32 &a_starts, const I32 &a_ends, const I32 &a_row_offsets,
                         const I32 &b_starts, const I32 &b_ends, const I32 &b_row_offsets) {
 Runs a = open_runs(a_starts, a_ends, a_row_offsets);
 Runs b = open_runs(b_starts, b_ends, b_row_offsets);
 same_height(a, b);
 std::vector<int32_t> s, e, row(size_t(a.h) + 1, 0);
 {
  py::gil_scoped_release release;
  for (int64_t r = 0; r < a.h; ++r) {
   row[size_t(r)] = int32_t(s.size());
   int32_t j = b.row[r];
   const int32_t jend = b.row[r + 1];
   for (int32_t i = a.row[r]; i < a.row[r + 1]; ++i) {
    while (j < jend && b.e[j] <= a.s[i]) ++j;
    int32_t cursor = a.s[i];
    int32_t k = j;
    while (k < jend && b.s[k] < a.e[i]) {
     if (b.s[k] > cursor) { s.push_back(cursor); e.push_back(b.s[k]); }
     if (b.e[k] > cursor) cursor = b.e[k];
     if (cursor >= a.e[i]) break;
     ++k;
    }
    if (cursor < a.e[i]) { s.push_back(cursor); e.push_back(a.e[i]); }
   }
  }
  row[size_t(a.h)] = int32_t(s.size());
 }
 return py::make_tuple(to_array(s), to_array(e), to_array(row));
}

// ---- connected components ----------------------------------------------

inline int32_t uf_find(std::vector<int32_t> &p, int32_t x) {
 while (p[size_t(x)] != x) { p[size_t(x)] = p[size_t(p[size_t(x)])]; x = p[size_t(x)]; }
 return x;
}

inline void uf_union(std::vector<int32_t> &p, int32_t a, int32_t b) {
 a = uf_find(p, a);
 b = uf_find(p, b);
 if (a == b) return;
 if (b < a) std::swap(a, b);
 p[size_t(b)] = a;   // parents always point at the smaller index, so a root is
                     // the minimum run index of its component
}

py::tuple run_ccl(const I32 &starts, const I32 &ends, const I32 &row_offsets, int64_t height) {
 Runs r = open_runs(starts, ends, row_offsets);
 if (height != r.h) throw std::invalid_argument("height must match the row offset table");
 std::vector<int32_t> parent(size_t(r.n)), label(size_t(r.n), 0);
 int32_t count = 0;
 {
  py::gil_scoped_release release;
  for (int64_t i = 0; i < r.n; ++i) parent[size_t(i)] = int32_t(i);
  // One merge walk against the previous row's run list, two pointers, O(runs).
  for (int64_t rr = 1; rr < r.h; ++rr) {
   int32_t i = r.row[rr], iend = r.row[rr + 1];
   int32_t j = r.row[rr - 1], jend = r.row[rr];
   while (i < iend && j < jend) {
    if (r.e[j] <= r.s[i]) { ++j; continue; }
    if (r.e[i] <= r.s[j]) { ++i; continue; }
    uf_union(parent, i, j);
    if (r.e[i] < r.e[j]) ++i; else ++j;
   }
  }
  // Relabel in row-major first-encounter order. Runs are already in row-major
  // order and a component's root is its minimum run index, so walking the runs
  // in order and numbering each root the first time it is seen reproduces
  // `scipy.ndimage.label` exactly: the first pixel of a component in C order is
  // the start of its first run.
  for (int64_t i = 0; i < r.n; ++i) {
   const int32_t root = uf_find(parent, int32_t(i));
   if (label[size_t(root)] == 0) label[size_t(root)] = ++count;
   label[size_t(i)] = label[size_t(root)];
  }
 }
 return py::make_tuple(to_array(label), count);
}

// ---- per-component reductions -------------------------------------------

// Equivalent to `np.bincount(labels.ravel(), minlength=count + 1)` over the
// dense label array. `panel_pixels` (h * w) fills bin 0 with the background
// pixel count the dense bincount reports; pass 0 to leave bin 0 empty. No
// consumer in validation.py reads bin 0, but reproducing it keeps the kernel
// exactly equal to the expression it replaces.
py::array_t<int64_t> run_counts(const I32 &starts, const I32 &ends, const I32 &labels,
                                int32_t count, int64_t panel_pixels) {
 if (starts.ndim() != 1 || ends.ndim() != 1 || starts.shape(0) != ends.shape(0))
  throw std::invalid_argument("run starts and ends differ in length");
 const int64_t n = starts.shape(0);
 const int32_t *l = open_labels(labels, n, count);
 const int32_t *s = starts.data(), *e = ends.data();
 if (panel_pixels < 0) throw std::invalid_argument("panel pixel count must be nonnegative");
 py::array_t<int64_t> out(py::ssize_t(count) + 1);
 int64_t *bins = out.mutable_data();
 {
  py::gil_scoped_release release;
  std::memset(bins, 0, size_t(count + 1) * sizeof(int64_t));
  int64_t total = 0;
  for (int64_t i = 0; i < n; ++i) {
   if (e[i] <= s[i]) throw std::invalid_argument("runs must be nonempty");
   const int64_t length = int64_t(e[i]) - int64_t(s[i]);
   bins[l[i]] += length;
   total += length;
  }
  if (total > panel_pixels && panel_pixels)
   throw std::invalid_argument("run length exceeds the stated panel pixel count");
  bins[0] = panel_pixels ? panel_pixels - total : 0;
 }
 return out;
}

// Equivalent to `_border_flags(labels, count + 1)` (validation.py:281). Bin 0 is
// set when any border pixel carries no label, which is what scattering through
// the concatenated border rows and columns of a dense label array does.
py::array_t<bool> run_border(const I32 &starts, const I32 &ends, const I32 &row_offsets,
                             const I32 &labels, int32_t count, int64_t width) {
 Runs r = open_runs(starts, ends, row_offsets);
 const int32_t *l = open_labels(labels, r.n, count);
 if (width < 0 || width > std::numeric_limits<int32_t>::max())
  throw std::invalid_argument("width must be a nonnegative int32");
 py::array_t<bool> out(py::ssize_t(count) + 1);
 bool *flags = out.mutable_data();
 {
  py::gil_scoped_release release;
  std::memset(flags, 0, size_t(count) + 1);
  if (r.h > 0 && width > 0) {
   int64_t covered = 0;
   for (int32_t i = r.row[0]; i < r.row[1]; ++i) { flags[l[i]] = true; covered += r.e[i] - r.s[i]; }
   if (covered < width) flags[0] = true;            // row 0 has unlabeled pixels
   covered = 0;
   for (int32_t i = r.row[r.h - 1]; i < r.row[r.h]; ++i) { flags[l[i]] = true; covered += r.e[i] - r.s[i]; }
   if (covered < width) flags[0] = true;            // row h-1 has unlabeled pixels
   for (int64_t rr = 0; rr < r.h; ++rr) {
    const int32_t a = r.row[rr], b = r.row[rr + 1];
    if (a == b) { flags[0] = true; continue; }      // whole row unlabeled
    if (r.s[a] == 0) flags[l[a]] = true; else flags[0] = true;
    if (r.e[b - 1] == width) flags[l[b - 1]] = true; else flags[0] = true;
   }
  }
 }
 return out;
}

// Equivalent to the chunked `np.bincount(labels[chunk][previous[chunk]], ...)`
// loop at validation.py:399-402, summed over the chunks. Bin 0 -- the pixels
// the predecessor occupies that carry no label here -- is reproduced too even
// though validation.py:403 never reads it.
py::array_t<int64_t> run_overlap(const I32 &starts, const I32 &ends, const I32 &row_offsets,
                                 const I32 &labels, int32_t count,
                                 const I32 &prev_starts, const I32 &prev_ends,
                                 const I32 &prev_row_offsets) {
 Runs c = open_runs(starts, ends, row_offsets);
 Runs p = open_runs(prev_starts, prev_ends, prev_row_offsets);
 same_height(c, p);
 const int32_t *l = open_labels(labels, c.n, count);
 py::array_t<int64_t> out(py::ssize_t(count) + 1);
 int64_t *bins = out.mutable_data();
 {
  py::gil_scoped_release release;
  std::memset(bins, 0, size_t(count + 1) * sizeof(int64_t));
  int64_t matched = 0, previous_total = 0;
  for (int64_t i = 0; i < p.n; ++i) previous_total += int64_t(p.e[i]) - int64_t(p.s[i]);
  for (int64_t r = 0; r < c.h; ++r) {
   int32_t i = c.row[r], iend = c.row[r + 1];
   int32_t j = p.row[r], jend = p.row[r + 1];
   while (i < iend && j < jend) {
    const int32_t lo = std::max(c.s[i], p.s[j]);
    const int32_t hi = std::min(c.e[i], p.e[j]);
    if (hi > lo) { bins[l[i]] += hi - lo; matched += hi - lo; }
    if (c.e[i] < p.e[j]) ++i; else ++j;
   }
  }
  bins[0] = previous_total - matched;
 }
 return out;
}

// `mask.sum()` (validation.py:392) as the sum of run lengths.
int64_t run_area(const I32 &starts, const I32 &ends) {
 if (starts.ndim() != 1 || ends.ndim() != 1 || starts.shape(0) != ends.shape(0))
  throw std::invalid_argument("run starts and ends differ in length");
 const int64_t n = starts.shape(0);
 const int32_t *s = starts.data(), *e = ends.data();
 int64_t total = 0;
 {
  py::gil_scoped_release release;
  for (int64_t i = 0; i < n; ++i) {
   if (e[i] <= s[i]) throw std::invalid_argument("runs must be nonempty");
   total += int64_t(e[i]) - int64_t(s[i]);
  }
 }
 return total;
}

// ---- VoidForest.merge pair extraction -----------------------------------

// The overlapping (before, after) label pairs of `VoidForest.merge`
// (validation.py:190-210), in the exact order that code unions them.
//
// The order is the product, not an incidental: `union` accumulates volumes and
// `trapped` in floating point, so the pairs, their order and the duplicates
// that arise when one pair straddles several row chunks are all part of the
// reported number. Three properties are therefore carried forward verbatim:
//
//   * the 64-row chunking, so a pair present in four chunks is emitted four
//     times, exactly as the dense scatter/flatnonzero does;
//   * dedup *within* a chunk only, with no state crossing a chunk boundary;
//   * ascending key order within a chunk. The dense key is
//     `before * (total + 1) + after` with `after <= total`, so ordering by that
//     key and ordering the pairs lexicographically are the same permutation.
//     Sorting the pairs directly reproduces it without the dense key table, so
//     this kernel has no `MERGE_KEY_TABLE_CAP` equivalent and no sort/scatter
//     fork: it is exact for any component count.
py::array_t<int32_t> run_pairs(const I32 &starts, const I32 &ends, const I32 &row_offsets,
                               const I32 &labels,
                               const I32 &prev_starts, const I32 &prev_ends,
                               const I32 &prev_row_offsets, const I32 &prev_labels,
                               int64_t chunk_rows) {
 Runs c = open_runs(starts, ends, row_offsets);
 Runs p = open_runs(prev_starts, prev_ends, prev_row_offsets);
 same_height(c, p);
 if (labels.ndim() != 1 || int64_t(labels.shape(0)) != c.n ||
     prev_labels.ndim() != 1 || int64_t(prev_labels.shape(0)) != p.n)
  throw std::invalid_argument("one label per run is required");
 if (chunk_rows < 1) throw std::invalid_argument("chunk_rows must be positive");
 const int32_t *cl = labels.data(), *pl = prev_labels.data();
 for (int64_t i = 0; i < c.n; ++i) if (cl[i] < 1) throw std::invalid_argument("run labels must be positive");
 for (int64_t i = 0; i < p.n; ++i) if (pl[i] < 1) throw std::invalid_argument("run labels must be positive");
 std::vector<int32_t> out;
 {
  py::gil_scoped_release release;
  std::vector<int64_t> keys;
  for (int64_t r0 = 0; r0 < c.h; r0 += chunk_rows) {
   const int64_t r1 = std::min(r0 + chunk_rows, c.h);
   keys.clear();
   for (int64_t r = r0; r < r1; ++r) {
    int32_t i = c.row[r], iend = c.row[r + 1];
    int32_t j = p.row[r], jend = p.row[r + 1];
    while (i < iend && j < jend) {
     const int32_t lo = std::max(c.s[i], p.s[j]);
     const int32_t hi = std::min(c.e[i], p.e[j]);
     if (hi > lo) keys.push_back((int64_t(pl[j]) << 32) | int64_t(uint32_t(cl[i])));
     if (c.e[i] < p.e[j]) ++i; else ++j;
    }
   }
   std::sort(keys.begin(), keys.end());
   keys.erase(std::unique(keys.begin(), keys.end()), keys.end());
   for (int64_t key : keys) {
    out.push_back(int32_t(key >> 32));
    out.push_back(int32_t(uint32_t(key & 0xffffffffll)));
   }
  }
 }
 py::array_t<int32_t> pairs({py::ssize_t(out.size() / 2), py::ssize_t(2)});
 if (!out.empty()) std::memcpy(pairs.mutable_data(), out.data(), out.size() * sizeof(int32_t));
 return pairs;
}

// ---- growth-check support ------------------------------------------------

// `block_any(mask, factor)` (validation.py:55) straight out of run space. The
// coarse panel is tiny (177x315 at the default decimation of 8 on the bracket),
// so it is materialised dense: `ndi.distance_transform_edt` consumes it as is,
// which is what keeps the coarse EDT bit-identical.
py::array_t<bool> run_block_any(const I32 &starts, const I32 &ends, const I32 &row_offsets,
                                int64_t width, int64_t factor) {
 Runs r = open_runs(starts, ends, row_offsets);
 if (width < 0 || width > std::numeric_limits<int32_t>::max())
  throw std::invalid_argument("width must be a nonnegative int32");
 if (factor < 1) throw std::invalid_argument("factor must be positive");
 for (int64_t i = 0; i < r.n; ++i)
  if (r.e[i] > width) throw std::invalid_argument("run extends past the panel width");
 const int64_t oh = (r.h + factor - 1) / factor, ow = (width + factor - 1) / factor;
 py::array_t<bool> out({py::ssize_t(oh), py::ssize_t(ow)});
 bool *cells = out.mutable_data();
 {
  py::gil_scoped_release release;
  std::memset(cells, 0, size_t(oh) * size_t(ow));
  for (int64_t rr = 0; rr < r.h; ++rr) {
   bool *line = cells + (rr / factor) * ow;
   for (int32_t i = r.row[rr]; i < r.row[rr + 1]; ++i) {
    const int64_t first = int64_t(r.s[i]) / factor, last = (int64_t(r.e[i]) - 1) / factor;
    for (int64_t cc = first; cc <= last; ++cc) line[cc] = true;
   }
  }
 }
 return out;
}

// ---- dense bridges (verification and the bounded diagnostic path) --------

// `_island_extent` (validation.py:265) without a dense label array: the first
// pixel in C order of a component is the start of its first run, and its pixel
// count is the sum of its run lengths. Called at most `max_examples` (128)
// times over a whole build. A component that is absent reports (0, 0, 0),
// which is what `argmax` over an all-false mask already did.
py::tuple run_extent(const I32 &starts, const I32 &ends, const I32 &row_offsets,
                     const I32 &labels, int32_t count, int32_t component) {
 Runs r = open_runs(starts, ends, row_offsets);
 const int32_t *l = open_labels(labels, r.n, count);
 int64_t row = 0, col = 0, pixels = 0;
 bool found = false;
 {
  py::gil_scoped_release release;
  for (int64_t rr = 0; rr < r.h; ++rr)
   for (int32_t i = r.row[rr]; i < r.row[rr + 1]; ++i) {
    if (l[i] != component) continue;
    if (!found) { found = true; row = rr; col = r.s[i]; }
    pixels += int64_t(r.e[i]) - int64_t(r.s[i]);
   }
 }
 return py::make_tuple(row, col, pixels);
}

// Materialise a dense int32 label array. Deliberately NOT on the hot path: the
// assessment measured 17.35 ms/layer (15.6 s over a 900-layer build) to write
// 14 MiB of int32 per layer, which by itself takes a 228x kernel down to 1.06x
// end to end. It exists so tests can compare against `scipy.ndimage.label`
// element by element, and for the dense fallback bridge.
py::array_t<int32_t> run_scatter(const I32 &starts, const I32 &ends, const I32 &row_offsets,
                                 const I32 &labels, int64_t width) {
 Runs r = open_runs(starts, ends, row_offsets);
 if (width < 0 || width > std::numeric_limits<int32_t>::max())
  throw std::invalid_argument("width must be a nonnegative int32");
 if (labels.ndim() != 1 || int64_t(labels.shape(0)) != r.n)
  throw std::invalid_argument("one label per run is required");
 for (int64_t i = 0; i < r.n; ++i)
  if (r.e[i] > width) throw std::invalid_argument("run extends past the panel width");
 const int32_t *l = labels.data();
 py::array_t<int32_t> out({py::ssize_t(r.h), py::ssize_t(width)});
 int32_t *pixels = out.mutable_data();
 {
  py::gil_scoped_release release;
  std::memset(pixels, 0, size_t(r.h) * size_t(width) * sizeof(int32_t));
  for (int64_t rr = 0; rr < r.h; ++rr) {
   int32_t *line = pixels + rr * width;
   for (int32_t i = r.row[rr]; i < r.row[rr + 1]; ++i)
    for (int32_t c = r.s[i]; c < r.e[i]; ++c) line[c] = l[i];
  }
 }
 return out;
}

}  // namespace

void bind_runs(py::module_ &m) {
 m.attr("RUN_TABLE_CAP") = RUN_TABLE_CAP;
 m.attr("RUN_DENSITY_FLOOR") = RUN_DENSITY_FLOOR;
 m.def("extract_runs", &extract_runs, py::arg("mask"), py::arg("want") = 1,
       py::arg("cap") = RUN_TABLE_CAP,
       "Row-wise runs where (mask != 0) == want; returns (starts, ends, row_offsets)");
 m.def("complement_runs", &complement_runs, py::arg("starts"), py::arg("ends"),
       py::arg("row_offsets"), py::arg("width"),
       "The opposite partition of the same transitions, in O(runs)");
 m.def("run_difference", &run_difference, py::arg("starts"), py::arg("ends"), py::arg("row_offsets"),
       py::arg("other_starts"), py::arg("other_ends"), py::arg("other_row_offsets"),
       "Runs of (a & ~b): the growth check's `mask & ~previous`");
 m.def("run_ccl", &run_ccl, py::arg("starts"), py::arg("ends"), py::arg("row_offsets"),
       py::arg("height"),
       "4-connected components over runs; returns (label per run, count) numbered as scipy.ndimage.label");
 m.def("run_counts", &run_counts, py::arg("starts"), py::arg("ends"), py::arg("labels"),
       py::arg("count"), py::arg("panel_pixels") = 0,
       "Per-component pixel counts == np.bincount(labels.ravel(), minlength=count + 1)");
 m.def("run_border", &run_border, py::arg("starts"), py::arg("ends"), py::arg("row_offsets"),
       py::arg("labels"), py::arg("count"), py::arg("width"),
       "Per-component panel-border flags == _border_flags(labels, count + 1)");
 m.def("run_overlap", &run_overlap, py::arg("starts"), py::arg("ends"), py::arg("row_offsets"),
       py::arg("labels"), py::arg("count"), py::arg("previous_starts"), py::arg("previous_ends"),
       py::arg("previous_row_offsets"),
       "Cross-layer overlap bincount over the predecessor's occupancy");
 m.def("run_area", &run_area, py::arg("starts"), py::arg("ends"),
       "Total set pixels == mask.sum()");
 m.def("run_pairs", &run_pairs, py::arg("starts"), py::arg("ends"), py::arg("row_offsets"),
       py::arg("labels"), py::arg("previous_starts"), py::arg("previous_ends"),
       py::arg("previous_row_offsets"), py::arg("previous_labels"), py::arg("chunk_rows") = 64,
       "VoidForest.merge's (before, after) pairs in union-call order, duplicates and chunking kept");
 m.def("run_block_any", &run_block_any, py::arg("starts"), py::arg("ends"), py::arg("row_offsets"),
       py::arg("width"), py::arg("factor"),
       "Block-maximum decimation == block_any(mask, factor)");
 m.def("run_extent", &run_extent, py::arg("starts"), py::arg("ends"), py::arg("row_offsets"),
       py::arg("labels"), py::arg("count"), py::arg("component"),
       "First pixel in C order and pixel count of one component == _island_extent");
 m.def("run_scatter", &run_scatter, py::arg("starts"), py::arg("ends"), py::arg("row_offsets"),
       py::arg("labels"), py::arg("width"),
       "Dense int32 label array; verification and fallback only, never the hot path");
}
