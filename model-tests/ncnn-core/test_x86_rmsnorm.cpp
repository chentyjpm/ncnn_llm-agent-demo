// Direct regression for the pinned ncnn x86 FP32 RMSNorm implementation.
// This is operator coverage, not trained-model inference or a benchmark.
// Build/run with run_x86_rmsnorm.py; no ncnn library or dispatcher is involved.

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <vector>

#if !defined(__i386__) && !defined(__x86_64__)
#error "This direct x86 regression requires an x86 GCC/Clang target"
#endif

#if defined(AGENT_RMSNORM_CPU_PROBE)

// This branch is compiled separately at the baseline x86-64 ISA. The runner
// calls it BEFORE starting any ISA-specific binary. GCC/Clang's CPU feature
// detection includes OS-enabled XSAVE state for AVX and AVX512.
int main(int argc, char** argv)
{
    if (argc != 2)
        return 1;
    __builtin_cpu_init();
    bool supported = false;
    if (std::strcmp(argv[1], "scalar") == 0)
        supported = true;
    else if (std::strcmp(argv[1], "sse2") == 0)
        supported = __builtin_cpu_supports("sse2");
    else if (std::strcmp(argv[1], "avx") == 0)
        supported = __builtin_cpu_supports("avx");
    else if (std::strcmp(argv[1], "avx-fma") == 0)
        supported = __builtin_cpu_supports("avx") && __builtin_cpu_supports("fma");
    else if (std::strcmp(argv[1], "avx512") == 0)
        supported = __builtin_cpu_supports("avx2") && __builtin_cpu_supports("fma")
                    && __builtin_cpu_supports("avx512f") && __builtin_cpu_supports("avx512dq")
                    && __builtin_cpu_supports("avx512bw") && __builtin_cpu_supports("avx512vl");
    else
        return 1;
    std::printf("%s ISA=%s CPU_AND_OS\n", supported ? "AVAILABLE" : "UNAVAILABLE", argv[1]);
    return supported ? 0 : 77;
}

#else

// Only the scalar source branch is selected differently: disabling SSE code
// generation globally on x86-64 would break its floating-point calling ABI.
// The runner also disables compiler auto-vectorization for this variant.
#if defined(AGENT_RMSNORM_SCALAR)
#undef __SSE2__
#endif

// Provide the platform convenience macro needed by x86_usability.h. Use the actual
// upstream headers unchanged, without fabricating a replacement implementation
// or requiring a generated ncnn platform.h/full ncnn build.
#define NCNN_FORCEINLINE inline __attribute__((__always_inline__))
#define NCNN_RUNTIME_CPU 0
namespace ncnn {
// Unrelated BF16 helpers in x86_usability.h name this mat.h function. A
// declaration suffices: no replacement definition is supplied, so accidentally
// calling it would fail at link time rather than run a fake implementation.
float bfloat16_to_float32(unsigned short value);
}
#include "x86_usability.h"
namespace ncnn {
#include "rmsnorm_fp32.h"
}

#if defined(AGENT_RMSNORM_SCALAR)
static const char* const variant = "scalar";
static const int max_pack = 1;
#elif __AVX512F__
static const char* const variant = "avx512";
static const int max_pack = 16;
#elif __AVX__ && __FMA__
static const char* const variant = "avx-fma";
static const int max_pack = 8;
#elif __AVX__
static const char* const variant = "avx";
static const int max_pack = 8;
#elif __SSE2__
static const char* const variant = "sse2";
static const int max_pack = 4;
#else
#error "Select AGENT_RMSNORM_SCALAR explicitly for the scalar source branch"
#endif

static const char* const patterns[] = {
    "lane-distinct", "mixed-magnitude", "chain-stripes", "constant-lanes", "zeros", "sparse-outliers"
};

static float input_value(int element, int lane, int pack, int count, int pattern)
{
    const int index = element * pack + lane;
    const float sign = ((element + lane) & 1) ? -1.f : 1.f;
    const float fraction = 0.5f + float((element * 13 + lane * 7) % 29) / 32.f;
    switch (pattern)
    {
    case 0:
        // Different per-lane means expose accidental horizontal reductions.
        return sign * std::ldexp(fraction, lane % 9 - 4);
    case 1:
        // All operands are finite, normal, and exactly representable in FP32.
        return sign * std::ldexp(fraction, (element * 7 + lane * 11) % 25 - 12);
    case 2:
        // Different magnitudes in adjacent independent accumulator blocks.
        return sign * std::ldexp(fraction, ((index / 4) % 4) * 4 - 6);
    case 3:
        // Include epsilon-dominated and ordinary constant lanes together.
        return sign * std::ldexp(1.f, (lane % 4) * 5 - 12);
    case 4:
        return 0.f;
    default:
        // A different outlier position for each lane also exercises tails.
        return sign * (element == (lane * 17 + count - 1) % count
                           ? std::ldexp(1.f, lane % 5 + 7)
                           : std::ldexp(fraction, -10));
    }
}

static int run_case(int count, int pack, bool affine, float eps, int pattern,
                    int offset, double& worst_ratio, int& printed_failures)
{
    const int size = count * pack;
    const float sentinel = -12345.625f;
    const int guard = 32;
    std::vector<float> storage(size + 2 * guard + 32, sentinel);
    const uintptr_t aligned = (reinterpret_cast<uintptr_t>(storage.data() + guard) + 63u) & ~uintptr_t(63u);
    float* const output = reinterpret_cast<float*>(aligned) + offset;
    const size_t begin = static_cast<size_t>(output - storage.data());
    std::vector<float> input(size);
    std::vector<float> gamma(count);
    std::vector<double> scales(pack);
    for (int element = 0; element < count; element++)
    {
        gamma[element] = float((element * 7 + 3) % 19 - 9) / 8.f;
        for (int lane = 0; lane < pack; lane++)
            input[element * pack + lane] = input_value(element, lane, pack, count, pattern);
    }
    std::copy(input.begin(), input.end(), output);

    // Independent reference: one DOUBLE sum for each packed lane, then an
    // ordinary sqrt. Do not reuse SIMD helpers, float reductions or rsqrt.
    for (int lane = 0; lane < pack; lane++)
    {
        double sum = 0.;
        for (int element = 0; element < count; element++)
        {
            const double value = input[element * pack + lane];
            sum += value * value;
        }
        scales[lane] = 1. / std::sqrt(sum / count + static_cast<double>(eps));
    }
    ncnn::rmsnorm(output, affine ? gamma.data() : 0, eps, count, pack);

    for (size_t i = 0; i < storage.size(); i++)
    {
        if ((i < begin || i >= begin + size) && storage[i] != sentinel)
        {
            std::fprintf(stderr, "BUFFER_CORRUPTION ISA=%s count=%d pack=%d offset=%d index=%zu\n",
                         variant, count, pack, offset, i);
            return 1;
        }
    }

    bool failed = false;
    for (int element = 0; element < count; element++)
    {
        for (int lane = 0; lane < pack; lane++)
        {
            const int index = element * pack + lane;
            const double expected = static_cast<double>(input[index]) * scales[lane]
                                    * (affine ? static_cast<double>(gamma[element]) : 1.);
            const double actual = output[index];
            // Float reduction/rsqrt and final multiplies, not FP16 tolerance.
            const double tolerance = 2e-6 + 2e-5 * std::fabs(expected);
            const double error = std::fabs(actual - expected);
            worst_ratio = std::max(worst_ratio, error / tolerance);
            if (!std::isfinite(actual) || error > tolerance)
            {
                failed = true;
                if (printed_failures++ < 12)
                    std::fprintf(stderr,
                                 "NUMERICAL_MISMATCH ISA=%s count=%d size=%d pack=%d affine=%d eps=%.9g pattern=%s offset=%d element=%d lane=%d actual=%.9g expected=%.17g tolerance=%.9g\n",
                                 variant, count, size, pack, affine, eps, patterns[pattern], offset,
                                 element, lane, actual, expected, tolerance);
            }
        }
    }
    return failed ? 2 : 0;
}

int main(int argc, char** argv)
{
    // The runner's expected name prevents a mislabeled build from satisfying
    // another ISA's coverage. Manual invocation must still use a hardware guard.
    if (argc != 2 || std::strcmp(argv[1], variant) != 0)
    {
        std::fprintf(stderr, "CONFIGURATION_ERROR compiled ISA=%s; use the guarded runner\n", variant);
        return 1;
    }
    const int counts[] = {
        1, 2, 3, 4, 5, 7, 8, 9, 15, 16, 17, 31, 32, 33, 47, 48, 49,
        63, 64, 65, 79, 95, 96, 97, 111, 127, 128, 129, 191, 255, 257, 511, 1023, 4097
    };
    const int packs[] = {1, 4, 8, 16};
    const float epsilons[] = {1e-6f, 1e-3f, 0.25f};
    int total = 0;
    int failed = 0;
    int printed_failures = 0;
    double worst_ratio = 0.;
    for (size_t p = 0; p < sizeof(packs) / sizeof(packs[0]); p++)
    {
        const int pack = packs[p];
        if (pack > max_pack)
        {
            std::printf("UNSUPPORTED_PACK ISA=%s pack=%d not_compiled\n", variant, pack);
            continue;
        }
        int cases = 0;
        for (size_t n = 0; n < sizeof(counts) / sizeof(counts[0]); n++)
            for (int affine = 0; affine < 2; affine++)
                for (size_t e = 0; e < sizeof(epsilons) / sizeof(epsilons[0]); e++)
                    for (int pattern = 0; pattern < 6; pattern++)
                        for (int offset = 0; offset < 2; offset++)
                        {
                            const int result = run_case(counts[n], pack, affine, epsilons[e], pattern,
                                                        offset, worst_ratio, printed_failures);
                            if (result == 1)
                                return 1;
                            failed += result != 0;
                            cases++;
                        }
        std::printf("EXECUTED ISA=%s pack=%d cases=%d\n", variant, pack, cases);
        total += cases;
    }
    std::printf("%s ISA=%s cases=%d failed=%d max_error_over_tolerance=%.9g\n",
                failed ? "FAIL" : "PASS", variant, total, failed, worst_ratio);
    return failed ? 2 : 0;
}
#endif
