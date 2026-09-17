// Geometric predicates and a bounding-volume hierarchy.
//
// This header replaces the CGAL facilities the analysis used to depend on
// (AABB_tree, box_self_intersection_d, do_intersect, coplanar,
// coplanar_orientation). Those packages are GPL-3.0-or-later, which the
// project's MIT license cannot carry; everything here is original and
// permissively licensed. See licenses/THIRD-PARTY.md.
//
// What mattered in the replacement is that CGAL's kernel evaluates its
// predicates *exactly*. Self-intersection classification turns on whether a
// determinant is exactly zero -- coplanar or not, same side of an edge or not
// -- and a plain double evaluation gets those wrong on the degenerate cases
// that shared mesh edges produce constantly. Every sign-critical determinant
// below is therefore accumulated in double-double arithmetic (an unevaluated
// hi+lo pair, ~106 bits). Constructions that only feed a magnitude, such as a
// distance, stay in plain double.
#pragma once
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <vector>

namespace vm {

struct Vec3 {
 double x = 0, y = 0, z = 0;
 double operator[](int i) const { return i == 0 ? x : (i == 1 ? y : z); }
 bool operator==(const Vec3 &o) const { return x == o.x && y == o.y && z == o.z; }
};
inline Vec3 operator-(const Vec3 &a, const Vec3 &b) { return {a.x - b.x, a.y - b.y, a.z - b.z}; }
inline Vec3 operator+(const Vec3 &a, const Vec3 &b) { return {a.x + b.x, a.y + b.y, a.z + b.z}; }
inline Vec3 operator*(const Vec3 &a, double s) { return {a.x * s, a.y * s, a.z * s}; }
inline double dot(const Vec3 &a, const Vec3 &b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
inline Vec3 cross(const Vec3 &a, const Vec3 &b) {
 return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x};
}
inline Vec3 midpoint(const Vec3 &a, const Vec3 &b) {
 return {(a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2};
}
inline double squared_distance(const Vec3 &a, const Vec3 &b) {
 const Vec3 d = a - b;
 return dot(d, d);
}

// --- compensated arithmetic ------------------------------------------------
// An unevaluated sum hi+lo with |lo| <= ulp(hi)/2, so the sign of the pair is
// the sign of hi whenever hi is nonzero. Inputs reach these routines as
// float32 coordinates widened to double: a difference of two of those needs at
// most ~25 bits, a product of two differences ~50, and a 3x3 determinant of
// such products ~78. All of that fits the ~106 bits carried here with room to
// spare, so the signs below are exact for the data this module is given.
struct DD {
 double hi = 0, lo = 0;
};

inline DD two_sum(double a, double b) {
 const double s = a + b, bb = s - a;
 return {s, (a - (s - bb)) + (b - bb)};
}

inline DD two_product(double a, double b) {
 const double p = a * b;
 return {p, std::fma(a, b, -p)};
}

inline DD dd_add(const DD &a, const DD &b) {
 const DD s = two_sum(a.hi, b.hi);
 return two_sum(s.hi, s.lo + a.lo + b.lo);
}

inline DD dd_sub(const DD &a, const DD &b) { return dd_add(a, DD{-b.hi, -b.lo}); }

inline DD dd_mul(const DD &a, const DD &b) {
 const DD p = two_product(a.hi, b.hi);
 return two_sum(p.hi, p.lo + (a.hi * b.lo + a.lo * b.hi));
}

inline int dd_sign(const DD &a) {
 if (a.hi > 0) return 1;
 if (a.hi < 0) return -1;
 if (a.lo > 0) return 1;
 if (a.lo < 0) return -1;
 return 0;
}

// Exact difference of two doubles, kept as a pair so nothing is lost before
// the products below.
inline DD exact_difference(double a, double b) { return two_sum(a, -b); }

// Sign of the 3x3 determinant | a-d ; b-d ; c-d |. Positive when a, b, c are
// counterclockwise seen from d, zero exactly when the four points are coplanar.
inline int orient3d(const Vec3 &a, const Vec3 &b, const Vec3 &c, const Vec3 &d) {
 const DD ax = exact_difference(a.x, d.x), ay = exact_difference(a.y, d.y), az = exact_difference(a.z, d.z);
 const DD bx = exact_difference(b.x, d.x), by = exact_difference(b.y, d.y), bz = exact_difference(b.z, d.z);
 const DD cx = exact_difference(c.x, d.x), cy = exact_difference(c.y, d.y), cz = exact_difference(c.z, d.z);
 const DD m0 = dd_sub(dd_mul(by, cz), dd_mul(bz, cy));
 const DD m1 = dd_sub(dd_mul(bx, cz), dd_mul(bz, cx));
 const DD m2 = dd_sub(dd_mul(bx, cy), dd_mul(by, cx));
 return dd_sign(dd_add(dd_sub(dd_mul(ax, m0), dd_mul(ay, m1)), dd_mul(az, m2)));
}

// Sign of the 2x2 determinant | a-c ; b-c | on the plane that drops `axis`.
inline int orient2d_dropping(const Vec3 &a, const Vec3 &b, const Vec3 &c, int axis) {
 const int u = (axis + 1) % 3, v = (axis + 2) % 3;
 const DD au = exact_difference(a[u], c[u]), av = exact_difference(a[v], c[v]);
 const DD bu = exact_difference(b[u], c[u]), bv = exact_difference(b[v], c[v]);
 return dd_sign(dd_sub(dd_mul(au, bv), dd_mul(av, bu)));
}

inline bool coplanar(const Vec3 &a, const Vec3 &b, const Vec3 &c, const Vec3 &d) {
 return orient3d(a, b, c, d) == 0;
}

// True when a, b and c lie on one line, which is also the degeneracy test for
// a triangle: its three vertices span no area.
inline bool collinear(const Vec3 &a, const Vec3 &b, const Vec3 &c) {
 for (int axis = 0; axis < 3; ++axis)
  if (orient2d_dropping(a, b, c, axis) != 0) return false;
 return true;
}

// The axis whose projection keeps a nondegenerate triangle nondegenerate: the
// one the normal points along most strongly. The plain-double normal only
// ranks the candidates; the choice is confirmed with the exact predicate, so a
// sliver whose approximate normal misleads still lands on a usable axis.
inline int dominant_axis(const Vec3 &a, const Vec3 &b, const Vec3 &c) {
 const Vec3 n = cross(b - a, c - a);
 const double magnitude[3] = {std::fabs(n.x), std::fabs(n.y), std::fabs(n.z)};
 int ranked[3] = {0, 1, 2};
 std::sort(ranked, ranked + 3, [&](int l, int r) { return magnitude[l] > magnitude[r]; });
 for (int i = 0; i < 3; ++i)
  if (orient2d_dropping(a, b, c, ranked[i]) != 0) return ranked[i];
 return ranked[0];  // Degenerate triangle; every projection collapses.
}

// CGAL's coplanar_orientation(p, q, r, s) for four coplanar points: POSITIVE
// (1) when r and s lie on the same side of the line pq, NEGATIVE (-1) when
// they lie on opposite sides, 0 when s is on that line.
inline int coplanar_orientation(const Vec3 &p, const Vec3 &q, const Vec3 &r, const Vec3 &s) {
 const int axis = dominant_axis(p, q, r);
 const int side_r = orient2d_dropping(p, q, r, axis);
 const int side_s = orient2d_dropping(p, q, s, axis);
 if (side_s == 0) return 0;
 return side_r == side_s ? 1 : -1;
}

struct Triangle {
 Vec3 v[3];
 const Vec3 &operator[](int i) const { return v[i]; }
 bool degenerate() const { return collinear(v[0], v[1], v[2]); }
};

// --- intersection predicates -----------------------------------------------

// Is c, already known to be collinear with ab, actually between a and b? Only
// the two projected coordinates matter once the axis is dropped.
inline bool between_on_line(const Vec3 &a, const Vec3 &b, const Vec3 &c, int axis) {
 for (int k = 1; k <= 2; ++k) {
  const int i = (axis + k) % 3;
  if (c[i] < std::min(a[i], b[i]) || c[i] > std::max(a[i], b[i])) return false;
 }
 return true;
}

// Do the closed 2D segments pq and rs share a point, once `axis` is dropped?
// Handles the collinear case, where sign agreement alone says nothing about
// whether the two spans actually overlap.
inline bool segments_meet_2d(const Vec3 &p, const Vec3 &q, const Vec3 &r, const Vec3 &s, int axis) {
 const int d1 = orient2d_dropping(p, q, r, axis);
 const int d2 = orient2d_dropping(p, q, s, axis);
 const int d3 = orient2d_dropping(r, s, p, axis);
 const int d4 = orient2d_dropping(r, s, q, axis);
 if (d1 * d2 < 0 && d3 * d4 < 0) return true;
 if (d1 == 0 && between_on_line(p, q, r, axis)) return true;
 if (d2 == 0 && between_on_line(p, q, s, axis)) return true;
 if (d3 == 0 && between_on_line(r, s, p, axis)) return true;
 if (d4 == 0 && between_on_line(r, s, q, axis)) return true;
 return false;
}

// Is x, already known to lie in the triangle's plane, inside the closed
// triangle? Consistent nonnegative or nonpositive turns around all three
// edges, which keeps points on an edge inside.
inline bool inside_coplanar_triangle(const Vec3 &x, const Vec3 &t0, const Vec3 &t1, const Vec3 &t2, int axis) {
 const int o0 = orient2d_dropping(t0, t1, x, axis);
 const int o1 = orient2d_dropping(t1, t2, x, axis);
 const int o2 = orient2d_dropping(t2, t0, x, axis);
 return (o0 >= 0 && o1 >= 0 && o2 >= 0) || (o0 <= 0 && o1 <= 0 && o2 <= 0);
}

// Does the closed segment pq meet the closed triangle t? Exact: the segment
// must straddle the triangle's plane, and must pass inside all three edges,
// both decided by orient3d signs alone.
inline bool segment_meets_triangle(const Vec3 &p, const Vec3 &q, const Triangle &t) {
 const int sp = orient3d(t[0], t[1], t[2], p);
 const int sq = orient3d(t[0], t[1], t[2], q);
 if (sp != 0 && sq != 0 && sp == sq) return false;
 if (sp == 0 && sq == 0) {
  // Segment lies in the triangle's plane; fall back to a 2D test there.
  const int axis = dominant_axis(t[0], t[1], t[2]);
  if (inside_coplanar_triangle(p, t[0], t[1], t[2], axis)) return true;
  if (inside_coplanar_triangle(q, t[0], t[1], t[2], axis)) return true;
  for (int i = 0; i < 3; ++i)
   if (segments_meet_2d(p, q, t[i], t[(i + 1) % 3], axis)) return true;
  return false;
 }
 // The segment crosses the plane once. It enters the triangle when it passes
 // the same side of all three edges, taken as planes through pq.
 int seen = 0;
 for (int i = 0; i < 3; ++i) {
  const int s = orient3d(p, q, t[i], t[(i + 1) % 3]);
  if (s == 0) continue;
  if (seen == 0) seen = s;
  else if (seen != s) return false;
 }
 return true;
}

// Do the two closed triangles share any point? Non-coplanar triangles meet
// exactly when an edge of one meets the other, which keeps the whole test on
// the exact segment predicate above. Coplanar pairs are decided in 2D.
inline bool triangles_meet(const Triangle &a, const Triangle &b) {
 const bool a_on_b = orient3d(b[0], b[1], b[2], a[0]) == 0 &&
                     orient3d(b[0], b[1], b[2], a[1]) == 0 &&
                     orient3d(b[0], b[1], b[2], a[2]) == 0;
 if (!a_on_b) {
  int sign = 0;
  bool straddles = false;
  for (int i = 0; i < 3 && !straddles; ++i) {
   const int s = orient3d(b[0], b[1], b[2], a[i]);
   if (s == 0) { straddles = true; break; }
   if (sign == 0) sign = s;
   else if (sign != s) straddles = true;
  }
  if (!straddles) return false;
  sign = 0;
  straddles = false;
  for (int i = 0; i < 3 && !straddles; ++i) {
   const int s = orient3d(a[0], a[1], a[2], b[i]);
   if (s == 0) { straddles = true; break; }
   if (sign == 0) sign = s;
   else if (sign != s) straddles = true;
  }
  if (!straddles) return false;
 }
 for (int i = 0; i < 3; ++i)
  if (segment_meets_triangle(a[i], a[(i + 1) % 3], b)) return true;
 for (int i = 0; i < 3; ++i)
  if (segment_meets_triangle(b[i], b[(i + 1) % 3], a)) return true;
 return false;
}

// --- point to triangle -----------------------------------------------------

inline double point_segment_squared_distance(const Vec3 &p, const Vec3 &a, const Vec3 &b) {
 const Vec3 ab = b - a, ap = p - a;
 const double len = dot(ab, ab);
 if (len <= 0) return dot(ap, ap);
 double t = dot(ap, ab) / len;
 t = t < 0 ? 0 : (t > 1 ? 1 : t);
 const Vec3 d = ap - ab * t;
 return dot(d, d);
}

// Distance to the closed triangle: the perpendicular drop when the foot lands
// inside, otherwise the nearest of the three edges.
inline double point_triangle_squared_distance(const Vec3 &p, const Triangle &t) {
 const Vec3 ab = t[1] - t[0], ac = t[2] - t[0], ap = p - t[0];
 const Vec3 n = cross(ab, ac);
 const double nn = dot(n, n);
 if (nn > 0) {
  const double beta = dot(cross(ap, ac), n) / nn;
  const double gamma = dot(cross(ab, ap), n) / nn;
  if (beta >= 0 && gamma >= 0 && beta + gamma <= 1) {
   const double drop = dot(ap, n);
   return drop * drop / nn;
  }
 }
 double best = point_segment_squared_distance(p, t[0], t[1]);
 best = std::min(best, point_segment_squared_distance(p, t[1], t[2]));
 return std::min(best, point_segment_squared_distance(p, t[2], t[0]));
}

// --- bounding volumes ------------------------------------------------------

struct Aabb {
 double lo[3] = {std::numeric_limits<double>::infinity(), std::numeric_limits<double>::infinity(),
                 std::numeric_limits<double>::infinity()};
 double hi[3] = {-std::numeric_limits<double>::infinity(), -std::numeric_limits<double>::infinity(),
                 -std::numeric_limits<double>::infinity()};

 void add(const Vec3 &p) {
  for (int i = 0; i < 3; ++i) {
   lo[i] = std::min(lo[i], p[i]);
   hi[i] = std::max(hi[i], p[i]);
  }
 }
 void add(const Aabb &o) {
  for (int i = 0; i < 3; ++i) {
   lo[i] = std::min(lo[i], o.lo[i]);
   hi[i] = std::max(hi[i], o.hi[i]);
  }
 }
 bool overlaps(const Aabb &o) const {
  for (int i = 0; i < 3; ++i)
   if (lo[i] > o.hi[i] || o.lo[i] > hi[i]) return false;
  return true;
 }
 double squared_distance(const Vec3 &p) const {
  double total = 0;
  for (int i = 0; i < 3; ++i) {
   const double d = p[i] < lo[i] ? lo[i] - p[i] : (p[i] > hi[i] ? p[i] - hi[i] : 0.0);
   total += d * d;
  }
  return total;
 }
 double center(int axis) const { return (lo[axis] + hi[axis]) / 2; }
};

inline Aabb triangle_bounds(const Triangle &t) {
 Aabb box;
 for (int i = 0; i < 3; ++i) box.add(t[i]);
 return box;
}

// A binary BVH over item boxes, split at the median centroid of the widest
// axis. Item order is permuted internally; callbacks always receive the
// caller's original index.
class Bvh {
 public:
 void build(const std::vector<Aabb> &boxes) {
  boxes_ = &boxes;
  order_.resize(boxes.size());
  for (size_t i = 0; i < boxes.size(); ++i) order_[i] = uint32_t(i);
  nodes_.clear();
  if (order_.empty()) return;
  nodes_.reserve(2 * order_.size());
  subdivide(0, uint32_t(order_.size()));
 }

 bool empty() const { return nodes_.empty(); }

 // Visit every item whose box overlaps `query`.
 template <class F>
 void overlaps(const Aabb &query, F &&visit) const {
  if (nodes_.empty()) return;
  uint32_t stack[64];
  int top = 0;
  stack[top++] = 0;
  while (top) {
   const Node &node = nodes_[stack[--top]];
   if (!node.box.overlaps(query)) continue;
   if (node.count) {
    for (uint32_t i = 0; i < node.count; ++i) visit(order_[node.start + i]);
   } else {
    stack[top++] = node.start;
    stack[top++] = node.right;
   }
  }
 }

 // Smallest squared distance from `p` to any item, where `item` returns the
 // squared distance to one item by original index.
 template <class F>
 double nearest_squared(const Vec3 &p, F &&item) const {
  double best = std::numeric_limits<double>::infinity();
  if (nodes_.empty()) return best;
  uint32_t stack[64];
  int top = 0;
  stack[top++] = 0;
  while (top) {
   const uint32_t index = stack[--top];
   const Node &node = nodes_[index];
   if (node.box.squared_distance(p) >= best) continue;
   if (node.count) {
    for (uint32_t i = 0; i < node.count; ++i) best = std::min(best, item(order_[node.start + i]));
   } else {
    // Descend into the nearer child first so the far one is likely pruned.
    const double left = nodes_[node.start].box.squared_distance(p);
    const double right = nodes_[node.right].box.squared_distance(p);
    if (left < right) {
     stack[top++] = node.right;
     stack[top++] = node.start;
    } else {
     stack[top++] = node.start;
     stack[top++] = node.right;
    }
   }
  }
  return best;
 }

 private:
 struct Node {
  Aabb box;
  uint32_t start = 0;   // leaf: first item; inner: left child node
  uint32_t count = 0;   // zero marks an inner node
  uint32_t right = 0;   // inner: right child node
 };

 static constexpr uint32_t kLeafSize = 8;

 uint32_t subdivide(uint32_t start, uint32_t count) {
  const uint32_t self = uint32_t(nodes_.size());
  nodes_.emplace_back();
  Aabb box;
  for (uint32_t i = 0; i < count; ++i) box.add((*boxes_)[order_[start + i]]);
  if (count <= kLeafSize) {
   nodes_[self].box = box;
   nodes_[self].start = start;
   nodes_[self].count = count;
   return self;
  }
  int axis = 0;
  double widest = -1;
  for (int i = 0; i < 3; ++i) {
   const double extent = box.hi[i] - box.lo[i];
   if (extent > widest) { widest = extent; axis = i; }
  }
  const uint32_t half = count / 2;
  const auto begin = order_.begin() + start;
  std::nth_element(begin, begin + half, begin + count, [&](uint32_t a, uint32_t b) {
   return (*boxes_)[a].center(axis) < (*boxes_)[b].center(axis);
  });
  const uint32_t left = subdivide(start, half);
  const uint32_t right = subdivide(start + half, count - half);
  nodes_[self].box = box;
  nodes_[self].start = left;
  nodes_[self].count = 0;
  nodes_[self].right = right;
  return self;
 }

 const std::vector<Aabb> *boxes_ = nullptr;
 std::vector<Node> nodes_;
 std::vector<uint32_t> order_;
};

}  // namespace vm
