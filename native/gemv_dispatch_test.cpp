#include "gemv_dispatch.h"
#include "kernel/x86/lm_head_x86.h"
#include <cpu.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <vector>

namespace {
int failures = 0;
int cases = 0;
constexpr float sentinel = 123456.f;

unsigned short to_bf16(float value)
{
    std::uint32_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    return static_cast<unsigned short>(bits >> 16);
}
float value_of(float value) { return value; }
float value_of(unsigned short value)
{
    const std::uint32_t bits = static_cast<std::uint32_t>(value) << 16;
    float result;
    std::memcpy(&result, &bits, sizeof(result));
    return result;
}
void check(bool ok, const char* detail)
{
    if (!ok) {
        ++failures;
        std::cerr << "FAIL: " << detail << '\n';
    }
}

template<class X, class W, class Run>
void verify(const char* name, const X* x, const W* w, int n, int k, Run run)
{
    ++cases;
    // Offset the output as well as inputs to exercise unaligned AVX loads/stores.
    std::vector<float> y(n + 2, sentinel);
    run(y.data() + 1);
    check(y.front() == sentinel && y.back() == sentinel, "output boundary overwritten");
    for (int i = 0; i < n; ++i) {
        double expected = 0, magnitude = 0;
        for (int j = 0; j < k; ++j) {
            const double term = static_cast<double>(value_of(x[j])) * value_of(w[i * k + j]);
            expected += term;
            magnitude += std::abs(term);
        }
        const double error = std::abs(static_cast<double>(y[i + 1]) - expected);
        if (!std::isfinite(y[i + 1]) || error > 1e-6 + 3e-6 * magnitude) {
            ++failures;
            std::cerr << "FAIL: " << name << " n=" << n << " k=" << k << " row=" << i
                      << " expected=" << expected << " actual=" << y[i + 1] << '\n';
        }
    }
}

void test_shape(int n, int k, int threads)
{
    std::vector<float> x_storage(k + 1), w_storage(n * k + 1);
    std::vector<unsigned short> xb_storage(k + 1), wb_storage(n * k + 1);
    float* x = x_storage.data() + 1;
    float* w = w_storage.data() + 1;
    unsigned short* xb = xb_storage.data() + 1;
    unsigned short* wb = wb_storage.data() + 1;
    for (int j = 0; j < k; ++j) {
        x[j] = static_cast<float>((j * 17 % 43) - 21) / 19.7f;
        xb[j] = to_bf16(x[j]);
    }
    for (int j = 0; j < n * k; ++j) {
        w[j] = static_cast<float>((j * 29 % 71) - 35) / 31.3f;
        wb[j] = to_bf16(w[j]);
    }
    verify("fp32 scalar", x, w, n, k, [&](float* y) {
        agent_gemv::fp32_scalar(x, w, y, n, k, threads);
    });
    verify("bf16 scalar", xb, wb, n, k, [&](float* y) {
        agent_gemv::bf16_scalar(xb, wb, y, n, k, threads);
    });
    verify("mixed scalar", x, wb, n, k, [&](float* y) {
        agent_gemv::bf16_fp32_scalar(x, wb, y, n, k, threads);
    });
    verify("fp32 forced fallback", x, w, n, k, [&](float* y) {
        agent_gemv::fp32_dispatch(x, w, y, n, k, threads, false);
    });
    verify("bf16 forced fallback", xb, wb, n, k, [&](float* y) {
        agent_gemv::bf16_dispatch(xb, wb, y, n, k, threads, false);
    });
    verify("mixed forced fallback", x, wb, n, k, [&](float* y) {
        agent_gemv::bf16_fp32_dispatch(x, wb, y, n, k, threads, false);
    });
    verify("fp32 automatic dispatch", x, w, n, k, [&](float* y) {
        ncnn_llm::gemv_fp32_x86(x, w, y, n, k, threads);
    });
    verify("bf16 automatic dispatch", xb, wb, n, k, [&](float* y) {
        ncnn_llm::gemv_bf16_x86(xb, wb, y, n, k, threads);
    });
    verify("mixed automatic dispatch", x, wb, n, k, [&](float* y) {
        ncnn_llm::gemv_bf16_fp32_x86(x, wb, y, n, k, threads);
    });
}

void test_invalid_inputs()
{
    float x = 1, w = 2, y = sentinel;
    unsigned short xb = to_bf16(x), wb = to_bf16(w);
    for (bool allow_avx2 : {false, true}) {
        for (int dimension : {-1, 0}) {
            agent_gemv::fp32_dispatch(&x, &w, &y, dimension, 1, 1, allow_avx2);
            agent_gemv::fp32_dispatch(&x, &w, &y, 1, dimension, 1, allow_avx2);
            agent_gemv::bf16_dispatch(&xb, &wb, &y, dimension, 1, 1, allow_avx2);
            agent_gemv::bf16_dispatch(&xb, &wb, &y, 1, dimension, 1, allow_avx2);
            agent_gemv::bf16_fp32_dispatch(&x, &wb, &y, dimension, 1, 1, allow_avx2);
            agent_gemv::bf16_fp32_dispatch(&x, &wb, &y, 1, dimension, 1, allow_avx2);
        }
        agent_gemv::fp32_dispatch(nullptr, &w, &y, 1, 1, 1, allow_avx2);
        agent_gemv::fp32_dispatch(&x, nullptr, &y, 1, 1, 1, allow_avx2);
        agent_gemv::fp32_dispatch(&x, &w, nullptr, 1, 1, 1, allow_avx2);
        agent_gemv::bf16_dispatch(nullptr, &wb, &y, 1, 1, 1, allow_avx2);
        agent_gemv::bf16_dispatch(&xb, nullptr, &y, 1, 1, 1, allow_avx2);
        agent_gemv::bf16_dispatch(&xb, &wb, nullptr, 1, 1, 1, allow_avx2);
        agent_gemv::bf16_fp32_dispatch(nullptr, &wb, &y, 1, 1, 1, allow_avx2);
        agent_gemv::bf16_fp32_dispatch(&x, nullptr, &y, 1, 1, 1, allow_avx2);
        agent_gemv::bf16_fp32_dispatch(&x, &wb, nullptr, 1, 1, 1, allow_avx2);
    }
    check(y == sentinel, "invalid input changed output");
}
}

int main()
{
    check(!agent_gemv::can_use_avx2(false, false), "baseline CPU selected AVX2");
    check(!agent_gemv::can_use_avx2(true, false), "AVX2 without FMA selected AVX2");
    check(!agent_gemv::can_use_avx2(false, true), "FMA without AVX2 selected AVX2");
    const bool compiled = agent_gemv::can_use_avx2(true, true);
    check(agent_gemv::runtime_has_avx2() ==
        (compiled && ncnn::cpu_support_x86_avx2() && ncnn::cpu_support_x86_fma()),
        "runtime dispatch does not match ncnn CPU/OS capabilities");
    // Keep automatic thread selection bounded on large CI machines.
    ncnn::set_omp_num_threads(2);
    for (int n : {1, 7, 8, 9, 17})
        for (int k : {1, 7, 8, 15, 16, 31, 32, 33, 65})
            for (int threads : {-1, 0, 1, 2}) test_shape(n, k, threads);
    test_invalid_inputs();
    std::cout << "GEMV numerical cases: " << cases << "; failures: " << failures
              << "; AVX2 compiled: " << compiled
              << "; runtime AVX2/FMA: " << agent_gemv::runtime_has_avx2() << '\n';
    return failures ? 1 : 0;
}
