// Index-parallel loops for the native kernels, with or without oneTBB.
//
// Release builds are not guaranteed to find TBB (it is optional in CMake), and
// without it every parallel kernel used to run on one core. The fallback here
// uses plain std::thread with dynamic index hand-out, so a build without TBB
// stays parallel. Either way the iterations must be independent: results are
// identical for any thread count.
//
// `WorkerLimit` caps the thread count for its lifetime, mirroring
// `resources.workers`. With TBB it is a `tbb::global_control`; without it, it
// sets `worker_limit`, which the fallback reads.
#pragma once
#include <algorithm>
#include <atomic>
#include <exception>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <vector>
#ifdef VOXELMILL_TBB
#include <tbb/blocked_range.h>
#include <tbb/global_control.h>
#include <tbb/parallel_for.h>
#endif

namespace voxelmill {

// 0 means "use every hardware thread".
inline std::atomic<int> worker_limit{0};

inline int fallback_threads(int n) {
 int limit = worker_limit.load();
 if (limit < 1) limit = int(std::max(1u, std::thread::hardware_concurrency()));
 return std::max(1, std::min(limit, n));
}

template<class F>
void parallel_n(int n, F &&fn) {
 if (n <= 0) return;
#ifdef VOXELMILL_TBB
 tbb::parallel_for(tbb::blocked_range<int>(0, n), [&](const tbb::blocked_range<int> &r) {
  for (int i = r.begin(); i < r.end(); ++i) fn(i);
 });
#else
 const int threads = fallback_threads(n);
 if (threads == 1) {
  for (int i = 0; i < n; ++i) fn(i);
  return;
 }
 std::atomic<int> next{0};
 std::exception_ptr failure;
 std::mutex failure_lock;
 auto work = [&]() {
  try {
   for (int i = next++; i < n; i = next++) fn(i);
  } catch (...) {
   std::lock_guard<std::mutex> hold(failure_lock);
   if (!failure) failure = std::current_exception();
   next = n;  // stop handing out work
  }
 };
 std::vector<std::thread> pool;
 pool.reserve(threads - 1);
 for (int t = 1; t < threads; ++t) pool.emplace_back(work);
 work();
 for (auto &thread : pool) thread.join();
 if (failure) std::rethrow_exception(failure);
#endif
}

// RAII worker ceiling, bound to Python as `_native.WorkerLimit(n)`.
class WorkerLimit {
public:
 explicit WorkerLimit(size_t n) {
  if (n < 1 || n > 32) throw std::invalid_argument("workers must be 1..32");
#ifdef VOXELMILL_TBB
  control_ = std::make_unique<tbb::global_control>(tbb::global_control::max_allowed_parallelism, n);
#else
  previous_ = worker_limit.exchange(int(n));
#endif
 }
 ~WorkerLimit() {
#ifndef VOXELMILL_TBB
  worker_limit = previous_;
#endif
 }
 WorkerLimit(const WorkerLimit &) = delete;
 WorkerLimit &operator=(const WorkerLimit &) = delete;
private:
#ifdef VOXELMILL_TBB
 std::unique_ptr<tbb::global_control> control_;
#else
 int previous_ = 0;
#endif
};

}  // namespace voxelmill
