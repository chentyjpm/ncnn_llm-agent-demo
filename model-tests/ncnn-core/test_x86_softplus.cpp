// Direct regression of unchanged upstream x86 Softplus FP32/BF16 kernels.
// Uses the actual ncnn Mat and Option, not substitute types or model inference.
// Run through run_x86_softplus.py so CPU/OS ISA guards precede execution.

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <limits>
#include <vector>

#if !defined(__x86_64__)
#error "This direct regression requires an x86-64 GCC/Clang target"
#endif

#if defined(AGENT_SOFTPLUS_CPU_PROBE)
#include <cpuid.h>

int main(int argc, char** argv)
{
    if (argc != 2)
        return 1;
    unsigned int a = 0, b = 0, c = 0, d = 0;
    __get_cpuid(1, &a, &b, &c, &d);
    const bool sse2 = (d & (1u << 26)) != 0;
    const bool fma = (c & (1u << 12)) != 0;
    const bool hw_avx = (c & (1u << 28)) != 0;
    uint64_t xcr0 = 0;
    if ((c & (1u << 26)) && (c & (1u << 27))) // XSAVE and OSXSAVE
    {
        unsigned int lo = 0, hi = 0;
        __asm__ volatile("xgetbv" : "=a"(lo), "=d"(hi) : "c"(0));
        xcr0 = (uint64_t(hi) << 32) | lo;
    }
    const bool avx = hw_avx && (xcr0 & 0x6) == 0x6;
    a = b = c = d = 0;
    __get_cpuid_count(7, 0, &a, &b, &c, &d);
    const unsigned int max_subleaf = a;
    const bool avx2 = avx && (b & (1u << 5));
    const unsigned int avx512_bits = (1u << 16) | (1u << 17) | (1u << 30) | (1u << 31);
    const bool avx512 = avx2 && fma && (b & avx512_bits) == avx512_bits && (xcr0 & 0xe6) == 0xe6;
    a = b = c = d = 0;
    if (max_subleaf >= 1)
        __get_cpuid_count(7, 1, &a, &b, &c, &d);
    const bool avx512bf16 = avx512 && (a & (1u << 5));
    const bool avxneconvert = avx2 && fma && (d & (1u << 5));
    a = b = c = d = 0;
    __get_cpuid(0x80000001, &a, &b, &c, &d);
    const bool fma4 = avx && (c & (1u << 16));
    bool supported = false;
    if (std::strcmp(argv[1], "scalar") == 0) supported = true;
    else if (std::strcmp(argv[1], "sse2") == 0) supported = sse2;
    else if (std::strcmp(argv[1], "avx") == 0) supported = avx;
    else if (std::strcmp(argv[1], "avx-fma") == 0) supported = avx && fma;
    else if (std::strcmp(argv[1], "avx-fma4") == 0) supported = fma4;
    else if (std::strcmp(argv[1], "avx2") == 0) supported = avx2 && fma;
    else if (std::strcmp(argv[1], "avx-ne-convert") == 0) supported = avxneconvert;
    else if (std::strcmp(argv[1], "avx512") == 0) supported = avx512;
    else if (std::strcmp(argv[1], "avx512-bf16") == 0) supported = avx512bf16;
    else return 1;
    std::printf("%s ISA=%s CPU_AND_OS xcr0=0x%llx\n", supported ? "AVAILABLE" : "UNAVAILABLE", argv[1],
                static_cast<unsigned long long>(xcr0));
    return supported ? 0 : 77;
}

#else

#include "mat.h"
#include "option.h"

// Select the scalar *source branch* without disabling the x86-64 float ABI.
// Mat is included first, so its declarations retain the normal platform ABI.
#if defined(AGENT_SOFTPLUS_SCALAR)
#undef __SSE2__
#endif

// Direct kernel coverage must not dispatch to a different ISA behind the test.
#undef NCNN_RUNTIME_CPU
#define NCNN_RUNTIME_CPU 0
#include "x86_usability.h"
#if __SSE2__
#include "sse_mathfun.h"
#if __AVX__
#include "avx_mathfun.h"
#if __AVX512F__
#include "avx512_mathfun.h"
#endif
#endif
#endif
namespace ncnn {
#include "softplus_fp32.h"
#include "softplus_bf16s.h"
}

#if defined(AGENT_SOFTPLUS_SCALAR)
static const char* const variant = "scalar";
static const int max_pack = 1;
#elif __AVX512BF16__
static const char* const variant = "avx512-bf16";
static const int max_pack = 16;
#elif __AVX512F__
static const char* const variant = "avx512";
static const int max_pack = 16;
#elif __AVXNECONVERT__
static const char* const variant = "avx-ne-convert";
static const int max_pack = 8;
#elif __AVX2__
static const char* const variant = "avx2";
static const int max_pack = 8;
#elif __FMA4__
static const char* const variant = "avx-fma4";
static const int max_pack = 8;
#elif __AVX__ && __FMA__
static const char* const variant = "avx-fma";
static const int max_pack = 8;
#elif __AVX__
static const char* const variant = "avx";
static const int max_pack = 8;
#else
static const char* const variant = "sse2";
static const int max_pack = 4;
#endif

static float float_from_bits(uint32_t bits)
{
    float value;
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}

static uint32_t float_bits(float value)
{
    uint32_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    return bits;
}

template<typename T> struct Storage;
template<> struct Storage<float>
{
    static const char* name() { return "fp32"; }
    static float encode(float value) { return value; }
    static float decode(float value) { return value; }
    static float sentinel() { return -12345.625f; }
    static double rounding_budget(double) { return 0.; }
    static double smallest() { return std::numeric_limits<float>::denorm_min(); }
};
template<> struct Storage<unsigned short>
{
    static const char* name() { return "bf16"; }
    // Input preparation only. Expected output never uses the ncnn conversion
    // routines or this encoder. Decode the stored input before referencing it.
    static unsigned short encode(float value)
    {
        uint32_t bits = float_bits(value);
        if ((bits & 0x7f800000u) != 0x7f800000u)
            bits += 0x8000u; // Input BF16 nearest, ties away from zero.
        return static_cast<unsigned short>(bits >> 16);
    }
    static float decode(unsigned short value) { return float_from_bits(uint32_t(value) << 16); }
    static unsigned short sentinel() { return 0xc640u; }
    static double rounding_budget(double value)
    {
        // BF16 has 7 fraction bits. Half of the numerical spacing is the
        // maximum nearest-rounding error, including at binade boundaries.
        int exponent = 0;
        std::frexp(value, &exponent);
        return std::ldexp(1., std::max(exponent - 9, -134));
    }
    static double smallest() { return std::ldexp(1., -133); }
};

static double reference(double x)
{
    // Stable independent double reference: neither float exp/log nor upstream
    // SIMD helpers; no overflow for FLT_MAX and no cancellation for x < 0.
    return std::max(x, 0.) + std::log1p(std::exp(-std::fabs(x)));
}

static float input_value(int pattern, int index)
{
    const float hi = 88.3762626647949f;
    const float inf = std::numeric_limits<float>::infinity();
    const float ordinary[] = {-16.f, -12.f, -8.f, -4.f, -2.f, -1.f, -0.01f, -0.f,
                              0.f, 0.01f, 0.5f, 1.f, 2.f, 4.f, 8.f, 16.f};
    const float boundary[] = {std::nextafter(hi, -inf), hi, std::nextafter(hi, inf),
                             88.f, 88.25f, 88.5f, 88.75f, 89.f, 90.f, 100.f,
                             128.f, 1024.f, 1e10f, std::numeric_limits<float>::max(),
                             std::nextafter(std::nextafter(hi, -inf), -inf),
                             std::nextafter(std::nextafter(hi, inf), inf)};
    const float specials[] = {inf, -inf, float_from_bits(0x7fc00001u), float_from_bits(0xffc00001u),
                              float_from_bits(0x7fffu << 16), float_from_bits(0xffffu << 16),
                              -std::numeric_limits<float>::max(), -1e10f, -1024.f, -128.f,
                              -100.f, -88.5f, float_from_bits(0x7f810000u), float_from_bits(0xff810000u), 0.f, -0.f};
    const float negative[] = {-104.f, -100.f, -92.f, -88.f, -80.f, -64.f, -40.f, -20.f,
                             -18.f, -17.f, -16.75f, -16.5f, -16.f, -15.f, -14.f, -10.f};
    const float* values[] = {ordinary, boundary, specials, negative};
    return values[pattern][index % 16];
}

struct Statistics
{
    long long elements;
    long long cancellations;
    int printed;
    double max_ratio;
    Statistics() : elements(0), cancellations(0), printed(0), max_ratio(0.) {}
};

template<typename T>
static int run_case(int dims, int w, int pack, int pattern, int rotation, int alignment_offset,
                    bool strict_negative, Statistics& stats, const ncnn::Option& opt)
{
    const int h = dims >= 2 ? 3 : 1;
    const int d = dims == 4 ? 2 : 1;
    const int channels = dims >= 3 ? 3 : 1;
    const int count = w * h * d * pack;
    const size_t elemsize = sizeof(T) * pack;
    const size_t channel_bytes = (size_t(count) * sizeof(T) + 15u) & ~size_t(15u);
    const size_t stride = channel_bytes / sizeof(T);
    const size_t guard = 64 / sizeof(T);
    std::vector<T> storage(stride * channels + 4 * guard, Storage<T>::sentinel());
    const uintptr_t aligned = (reinterpret_cast<uintptr_t>(storage.data() + guard) + 63u) & ~uintptr_t(63u);
    // SSE FP32 requires 16-byte aligned loads. Exercise 64 and 16 mod 64,
    // never manufacture misaligned input outside the real Mat contract.
    T* data = reinterpret_cast<T*>(aligned + alignment_offset);
    const size_t begin = data - storage.data();
    ncnn::Mat mat;
    if (dims == 1) mat = ncnn::Mat(w, data, elemsize, pack);
    if (dims == 2) mat = ncnn::Mat(w, h, data, elemsize, pack);
    if (dims == 3) mat = ncnn::Mat(w, h, channels, data, elemsize, pack);
    if (dims == 4) mat = ncnn::Mat(w, h, d, channels, data, elemsize, pack);
    if (mat.cstep * pack != stride || mat.elembits() != int(sizeof(T) * 8))
    {
        std::fprintf(stderr, "MAT_LAYOUT_ERROR dims=%d w=%d pack=%d cstep=%zu stride=%zu\n", dims, w, pack, mat.cstep, stride);
        return 1;
    }
    std::vector<float> inputs(count * channels);
    for (int q = 0; q < channels; q++)
        for (int i = 0; i < count; i++)
        {
            const T value = Storage<T>::encode(input_value(pattern, i + q * 7 + rotation));
            data[q * stride + i] = value;
            inputs[q * count + i] = Storage<T>::decode(value);
        }
    if (sizeof(T) == 4) ncnn::softplus_fp32(mat, opt);
    else ncnn::softplus_bf16s(mat, opt);

    for (size_t i = 0; i < storage.size(); i++)
    {
        const bool active = i >= begin && i < begin + stride * channels && (i - begin) % stride < size_t(count);
        if (!active && storage[i] != Storage<T>::sentinel())
        {
            std::fprintf(stderr, "BUFFER_CORRUPTION ISA=%s dtype=%s dims=%d w=%d pack=%d index=%zu\n",
                         variant, Storage<T>::name(), dims, w, pack, i);
            return 1;
        }
    }
    bool failed = false;
    for (int q = 0; q < channels; q++)
        for (int i = 0; i < count; i++)
        {
            const float x = inputs[q * count + i];
            const float actual = Storage<T>::decode(data[q * stride + i]);
            const double expected = reference(x);
            // Fixed budget chosen before execution: one FP32 near-one addition
            // rounding, SIMD exp/log error, and (only for BF16) storage rounding.
            // Absolute error does NOT establish relative accuracy in tiny tails.
            const double tolerance = 1.2e-7 + 2e-6 * std::fabs(expected)
                                     + Storage<T>::rounding_budget(expected);
            bool mismatch = false;
            if (std::isnan(x)) mismatch = !std::isnan(actual);
            else if (x > 88.3762626647949f) mismatch = float_bits(actual) != float_bits(x);
            else if (x == -std::numeric_limits<float>::infinity()) mismatch = float_bits(actual) != 0;
            else
            {
                const double error = std::fabs(double(actual) - expected);
                mismatch = !std::isfinite(actual) || actual < 0.f || error > tolerance;
                stats.max_ratio = std::max(stats.max_ratio, error / tolerance);
                if (x < 0.f && actual == 0.f && expected >= Storage<T>::smallest())
                {
                    stats.cancellations++;
                    if (strict_negative) mismatch = true;
                }
            }
            stats.elements++;
            if (mismatch)
            {
                failed = true;
                if (stats.printed++ < 12)
                    std::fprintf(stderr, "NUMERICAL_MISMATCH ISA=%s dtype=%s dims=%d w=%d h=%d d=%d channels=%d pack=%d pattern=%d rotation=%d alignment=%d channel=%d index=%d input=%.9g actual=%.9g expected=%.17g tolerance=%.9g strict_negative=%d\n",
                                 variant, Storage<T>::name(), dims, w, h, d, channels, pack, pattern, rotation,
                                 alignment_offset, q, i, x, actual, expected, tolerance, strict_negative);
            }
        }
    return failed ? 2 : 0;
}

template<typename T>
static int run_storage(bool strict_negative, const ncnn::Option& opt)
{
    const int widths[] = {1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,
                          25,26,27,28,29,30,31,32,33,47,48,49,63,64,65,127,129,257};
    const int packs[] = {1,4,8,16};
    int total = 0, failed = 0;
    Statistics stats;
    for (size_t p = 0; p < sizeof(packs) / sizeof(packs[0]); p++)
    {
        const int pack = packs[p];
        if (pack > max_pack)
        {
            std::printf("UNSUPPORTED_PACK ISA=%s dtype=%s pack=%d not_compiled\n", variant, Storage<T>::name(), pack);
            continue;
        }
        int cases = 0;
        for (int dims = 1; dims <= 4; dims++)
            for (size_t w = 0; w < sizeof(widths) / sizeof(widths[0]); w++)
                for (int pattern = 0; pattern < 4; pattern++)
                    for (int rotation = 0; rotation < 16; rotation++)
                        for (int offset = 0; offset <= 16; offset += 16)
                        {
                            const int result = run_case<T>(dims, widths[w], pack, pattern, rotation, offset,
                                                           strict_negative, stats, opt);
                            if (result == 1) return 1;
                            failed += result != 0;
                            cases++;
                        }
        std::printf("EXECUTED ISA=%s dtype=%s pack=%d dims=1,2,3,4 cases=%d\n", variant, Storage<T>::name(), pack, cases);
        total += cases;
    }
    std::printf("ACCURACY_LIMITATION ISA=%s dtype=%s representable_negative_results_rounded_to_zero=%lld strict_negative=%d\n",
                variant, Storage<T>::name(), stats.cancellations, strict_negative);
    std::printf("%s ISA=%s dtype=%s cases=%d failed=%d elements=%lld max_error_over_tolerance=%.9g\n",
                failed ? "FAIL" : "PASS", variant, Storage<T>::name(), total, failed, stats.elements, stats.max_ratio);
    return failed ? 2 : 0;
}

int main(int argc, char** argv)
{
    if ((argc != 2 && argc != 3) || std::strcmp(argv[1], variant) != 0
        || (argc == 3 && std::strcmp(argv[2], "--strict-negative") != 0))
    {
        std::fprintf(stderr, "CONFIGURATION_ERROR compiled ISA=%s; use guarded runner\n", variant);
        return 1;
    }
    ncnn::Option opt;
    opt.num_threads = 1;
    opt.use_bf16_storage = true;
    // Direct calls do not use Net's flush_denormals option. Explicitly preserve
    // subnormals so the negative-tail diagnostic is reproducible.
    unsigned int mxcsr;
    __asm__ volatile("stmxcsr %0" : "=m"(mxcsr));
    const unsigned int ieee_mxcsr = mxcsr & ~0x8040u;
    __asm__ volatile("ldmxcsr %0" : : "m"(ieee_mxcsr));
    const int fp32 = run_storage<float>(argc == 3, opt);
    const int bf16 = run_storage<unsigned short>(argc == 3, opt);
    __asm__ volatile("ldmxcsr %0" : : "m"(mxcsr));
    if (fp32 == 1 || bf16 == 1) return 1;
    return fp32 || bf16 ? 2 : 0;
}
#endif
