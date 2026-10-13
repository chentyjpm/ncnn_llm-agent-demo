#include "gemv_dispatch.h"
#include "gemv_openmp_compat.h"
#include "kernel/x86/lm_head_x86.h"

#include <cpu.h>
#include <cstddef>
#include <cstdint>
#include <cstring>

#ifndef AGENT_GEMV_HAS_AVX2
#define AGENT_GEMV_HAS_AVX2 0
#endif

#if AGENT_GEMV_HAS_AVX2
namespace ncnn_llm {
void gemv_fp32_x86_avx2(const float*, const float*, float*, int, int, int);
void gemv_bf16_x86_avx2(const unsigned short*, const unsigned short*, float*, int, int, int);
void gemv_bf16_fp32_x86_avx2(const float*, const unsigned short*, float*, int, int, int);
}
#endif

namespace agent_gemv {
namespace {
float as_float(float value) { return value; }
float as_float(unsigned short value)
{
    static_assert(sizeof(unsigned short) == 2 && sizeof(float) == 4);
    const std::uint32_t bits = static_cast<std::uint32_t>(value) << 16;
    float result;
    std::memcpy(&result, &bits, sizeof(result));
    return result;
}

template<class Input, class Weight>
void scalar(const Input* x, const Weight* weight, float* logits, int n, int k, int threads)
{
    // Match the upstream GEMV contract, including no writes for empty inputs.
    if (n <= 0 || k <= 0 || !x || !weight || !logits) return;
    if (threads <= 0) threads = agent_gemv_max_threads();
#if defined(_OPENMP) && NCNN_OPENMP
#pragma omp parallel for num_threads(threads) schedule(static)
#endif
    for (int i = 0; i < n; ++i) {
        const Weight* row = weight + static_cast<std::size_t>(i) * k;
        float sum = 0.f;
        for (int j = 0; j < k; ++j) sum += as_float(x[j]) * as_float(row[j]);
        logits[i] = sum;
    }
}
}

bool can_use_avx2(bool cpu_avx2, bool cpu_fma)
{
    return AGENT_GEMV_HAS_AVX2 && cpu_avx2 && cpu_fma;
}

bool runtime_has_avx2()
{
#if AGENT_GEMV_HAS_AVX2
    // ncnn checks the OS's AVX register-state support as well as CPU features.
    return can_use_avx2(ncnn::cpu_support_x86_avx2(), ncnn::cpu_support_x86_fma());
#else
    return false;
#endif
}

void fp32_scalar(const float* x, const float* w, float* y, int n, int k, int threads)
{ scalar(x, w, y, n, k, threads); }
void bf16_scalar(const unsigned short* x, const unsigned short* w, float* y, int n, int k, int threads)
{ scalar(x, w, y, n, k, threads); }
void bf16_fp32_scalar(const float* x, const unsigned short* w, float* y, int n, int k, int threads)
{ scalar(x, w, y, n, k, threads); }

void fp32_dispatch(const float* x, const float* w, float* y, int n, int k, int threads, bool allow_avx2)
{
#if AGENT_GEMV_HAS_AVX2
    if (allow_avx2 && runtime_has_avx2()) {
        ncnn_llm::gemv_fp32_x86_avx2(x, w, y, n, k, threads);
        return;
    }
#endif
    fp32_scalar(x, w, y, n, k, threads);
}
void bf16_dispatch(const unsigned short* x, const unsigned short* w, float* y, int n, int k, int threads, bool allow_avx2)
{
#if AGENT_GEMV_HAS_AVX2
    if (allow_avx2 && runtime_has_avx2()) {
        ncnn_llm::gemv_bf16_x86_avx2(x, w, y, n, k, threads);
        return;
    }
#endif
    bf16_scalar(x, w, y, n, k, threads);
}
void bf16_fp32_dispatch(const float* x, const unsigned short* w, float* y, int n, int k, int threads, bool allow_avx2)
{
#if AGENT_GEMV_HAS_AVX2
    if (allow_avx2 && runtime_has_avx2()) {
        ncnn_llm::gemv_bf16_fp32_x86_avx2(x, w, y, n, k, threads);
        return;
    }
#endif
    bf16_fp32_scalar(x, w, y, n, k, threads);
}
}

namespace ncnn_llm {
void gemv_fp32_x86(const float* x, const float* w, float* y, int n, int k, int threads)
{ agent_gemv::fp32_dispatch(x, w, y, n, k, threads, true); }
void gemv_bf16_x86(const unsigned short* x, const unsigned short* w, float* y, int n, int k, int threads)
{ agent_gemv::bf16_dispatch(x, w, y, n, k, threads, true); }
void gemv_bf16_fp32_x86(const float* x, const unsigned short* w, float* y, int n, int k, int threads)
{ agent_gemv::bf16_fp32_dispatch(x, w, y, n, k, threads, true); }
}
