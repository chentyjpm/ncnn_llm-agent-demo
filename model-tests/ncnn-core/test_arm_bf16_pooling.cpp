// Strict execution test for the upstream ARM BF16 pack4 3x3 max-pool kernel.
// Direct invocation prevents a generic layer/CPU fallback from passing the gate.
// This synthetic operator test is not trained-model or performance evidence.
#include "cpu.h"
#include "mat.h"
#include "option.h"

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <limits>

#if defined(__aarch64__) && __ARM_NEON && NCNN_BF16 && NCNN_GNU_INLINE_ASM
#include "arm_usability.h"
namespace ncnn {
#include "pooling_3x3_pack4_bf16s.h"
}

static unsigned short to_bf16(float value)
{
    std::uint32_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    return static_cast<unsigned short>(bits >> 16);
}

static float from_bf16(unsigned short value)
{
    const std::uint32_t bits = static_cast<std::uint32_t>(value) << 16;
    float result;
    std::memcpy(&result, &bits, sizeof(result));
    return result;
}

static float input_value(int x, int y, int channel, int pattern)
{
    // A two-column remainder starts at source x % 8 == 0. Its second
    // window must select the fifth source column (x % 8 == 4), whose three
    // row loads were broken. Distinct lanes prevent a raw FP32 load from
    // accidentally matching after it reinterprets pairs of BF16 elements.
    const int row_rank = (y + channel / 4) % 3;
    const int background = (x * 7 + y * 11 + channel * 5) % 31 + 1;
    const bool peak = x % 8 == 4;
    int value;
    if (pattern == 0)
        value = peak ? 64 + row_rank * 16 + channel : background;
    else if (pattern == 1)
        value = peak ? -((2 - row_rank) * 16 + channel + 1) : -64 - background;
    else
        value = peak ? 32 + row_rank * 16 + channel
                     : ((x + y + channel) % 2 ? background : -background);
    // Integer multiples of 1/16 with magnitudes below 128/16 are exactly
    // representable in both FP32 and BF16. No rounding tolerance is needed.
    return static_cast<float>(value) / 16.f;
}

static int run_case(int w, int h, int channels, int pattern,
                    unsigned long long& checked_values,
                    unsigned long long& changed_load_values)
{
    const int groups = channels / 4;
    const int outw = (w - 3) / 2 + 1;
    const int outh = (h - 3) / 2 + 1;
    if (channels % 4 || w < 5 || h < 4 || h % 2 ||
        (outw % 4 != 2 && outw % 4 != 3))
    {
        std::fprintf(stderr, "ERROR: invalid two-column-tail fixture\n");
        return 1;
    }
    ncnn::Mat input(w, h, groups, static_cast<size_t>(8), 4);
    ncnn::Mat output(outw, outh, groups, static_cast<size_t>(8), 4);
    if (input.empty() || output.empty())
    {
        std::fprintf(stderr, "ERROR: allocation failed\n");
        return 1;
    }
    if (input.elempack != 4 || output.elempack != 4 ||
        input.elembits() != 16 || output.elembits() != 16 ||
        input.elemsize != 8 || output.elemsize != 8)
    {
        std::fprintf(stderr, "ERROR: expected BF16 pack4 input/output\n");
        return 1;
    }

    for (int g = 0; g < groups; g++)
    {
        ncnn::Mat in = input.channel(g);
        ncnn::Mat out = output.channel(g);
        for (int y = 0; y < h; y++)
            for (int x = 0; x < w; x++)
                for (int lane = 0; lane < 4; lane++)
                {
                    const float value = input_value(x, y, g * 4 + lane, pattern);
                    const unsigned short bits = to_bf16(value);
                    if (!std::isfinite(value) || from_bf16(bits) != value)
                    {
                        std::fprintf(stderr, "ERROR: fixture is not exactly representable finite BF16\n");
                        return 1;
                    }
                    in.row<unsigned short>(y)[x * 4 + lane] = bits;
                }
        for (int y = 0; y < outh; y++)
            for (int x = 0; x < outw * 4; x++)
                out.row<unsigned short>(y)[x] = 0x7fc1; // BF16 quiet NaN: unwritten outputs fail.
    }

    ncnn::Option opt;
    opt.num_threads = 1;
    opt.use_packing_layout = true;
    opt.use_bf16_storage = true;
    opt.use_fp16_storage = false;
    opt.use_fp16_arithmetic = false;
    // No layer dispatcher, storage conversion, or fallback is involved here.
    ncnn::pooling3x3s2_max_pack4_bf16s_neon(input, output, opt);

    int mismatches = 0;
    const int changed_load_column = outw - outw % 4 + 1;
    for (int c = 0; c < channels; c++)
    {
        const ncnn::Mat out = output.channel(c / 4);
        for (int y = 0; y < outh; y++)
            for (int x = 0; x < outw; x++)
            {
                float expected = -std::numeric_limits<float>::infinity();
                // Independent scalar FP32 reference, regenerated from logical
                // coordinates rather than the packed input under test.
                for (int ky = 0; ky < 3; ky++)
                    for (int kx = 0; kx < 3; kx++)
                    {
                        const float value = input_value(x * 2 + kx, y * 2 + ky, c, pattern);
                        if (value > expected)
                            expected = value;
                    }
                const unsigned short expected_bits = to_bf16(expected);
                const unsigned short actual_bits = out.row<const unsigned short>(y)[x * 4 + c % 4];
                const float actual = from_bf16(actual_bits);
                checked_values++;
                if (x == changed_load_column)
                    changed_load_values++;
                if (!std::isfinite(actual) || actual != expected || actual_bits != expected_bits)
                {
                    if (mismatches++ < 3)
                        std::fprintf(stderr,
                                     "NUMERICAL_MISMATCH w=%d h=%d c=%d pattern=%d outw=%d x=%d y=%d channel=%d actual=%.9g expected=%.9g bits=%04x expected_bits=%04x changed_load=%d\n",
                                     w, h, channels, pattern, outw, x, y, c, actual, expected,
                                     static_cast<unsigned int>(actual_bits), static_cast<unsigned int>(expected_bits),
                                     x == changed_load_column);
                }
            }
    }
    return mismatches ? 2 : 0;
}
#endif

int main()
{
#if !defined(__aarch64__) || !__ARM_NEON || !NCNN_BF16 || !NCNN_GNU_INLINE_ASM
    std::fprintf(stderr, "ERROR: AArch64 NEON build with BF16 storage and GNU inline assembly is required; no skip or fallback\n");
    return 1;
#else
    if (!ncnn::cpu_support_arm_neon())
    {
        std::fprintf(stderr, "ERROR: AArch64 NEON hardware is required; no skip or fallback\n");
        return 1;
    }
    // BF16 storage uses NEON FP32 arithmetic, not the optional BF16 arithmetic
    // ISA. Require an ARM64 runner; x86 builds must fail rather than skip.
    // Include 149->74 and 37->18, bounded 6/7-column outputs, and 2/3-column
    // outputs without a four-column bulk loop. Every width has remainder 2/3.
    // Even input heights leave an unused last row, so the old erroneous 16-byte
    // load at an odd-width row end stays in the allocation for negative control.
    const int shapes[][2] = {{149, 10}, {151, 8}, {37, 8}, {39, 6},
                            {13, 6}, {15, 4}, {6, 4}, {7, 6}};
    const int shape_count = static_cast<int>(sizeof(shapes) / sizeof(shapes[0]));
    int result = 0;
    int cases = 0;
    unsigned long long checked_values = 0;
    unsigned long long changed_load_values = 0;
    for (int i = 0; i < shape_count; i++)
        for (int channels = 4; channels <= 12; channels += 4)
            for (int pattern = 0; pattern < 3; pattern++)
            {
                const int ret = run_case(shapes[i][0], shapes[i][1], channels, pattern,
                                         checked_values, changed_load_values);
                if (ret == 1)
                    return 1;
                if (ret == 2)
                    result = 2;
                cases++;
            }
    if (result)
        return result;
    std::printf("PASS: %d direct ARM BF16 pack4 max-pooling stride-2 cases, %llu exact scalar comparisons, %llu changed-load comparisons; no fallback\n",
                cases, checked_values, changed_load_values);
    return 0;
#endif
}
