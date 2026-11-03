// Strict execution test for the upstream ARM FP16 pack8 stride-2 kernel.
// Direct invocation prevents a generic layer/CPU fallback from passing the gate.
// This synthetic operator test is not trained-model or performance evidence.
#include "cpu.h"
#include "mat.h"
#include "option.h"

#include <cmath>
#include <cstdio>
#include <limits>

#if defined(__aarch64__) && defined(__ARM_FEATURE_FP16_VECTOR_ARITHMETIC)
#include <arm_neon.h>
namespace ncnn {
#include "convolutiondepthwise_3x3_pack8_fp16s.h"
}

static float input_value(int x, int y, int channel)
{
    return static_cast<float>((y * 7 + x * 3 + channel * 5) % 31 - 15) / 16.f;
}

static float weight_value(int k, int channel)
{
    return static_cast<float>((k + channel) % 4 + 1) / 16.f;
}

static float bias_value(int channel)
{
    return static_cast<float>(channel % 8 - 4) / 16.f;
}

static int run_case(int w, int h, int pad, int channels, bool with_bias,
                    unsigned long long& checked_values)
{
    const int groups = channels / 8;
    const int padded_w = w + pad * 2;
    const int padded_h = h + pad * 2;
    const int outw = (padded_w - 3) / 2 + 1;
    const int outh = (padded_h - 3) / 2 + 1;
    ncnn::Mat input(padded_w, padded_h, groups, static_cast<size_t>(16), 8);
    ncnn::Mat output(outw, outh, groups, static_cast<size_t>(16), 8);
    // The kernel consumes nine pack8 values per channel group, stored by row.
    ncnn::Mat weights(9, groups, static_cast<size_t>(16), 8);
    ncnn::Mat bias;
    if (with_bias)
        bias.create(channels, static_cast<size_t>(2));
    if (input.empty() || output.empty() || weights.empty() || (with_bias && bias.empty()))
    {
        std::fprintf(stderr, "ERROR: allocation failed\n");
        return 1;
    }
    if (input.elempack != 8 || output.elempack != 8 || input.elembits() != 16 || output.elembits() != 16)
    {
        std::fprintf(stderr, "ERROR: expected FP16 pack8 input/output\n");
        return 1;
    }

    for (int g = 0; g < groups; g++)
    {
        ncnn::Mat in = input.channel(g);
        ncnn::Mat out = output.channel(g);
        for (int y = 0; y < padded_h; y++)
        {
            __fp16* row = in.row<__fp16>(y);
            for (int x = 0; x < padded_w; x++)
                for (int lane = 0; lane < 8; lane++)
                    row[x * 8 + lane] = static_cast<__fp16>(
                        x >= pad && x < w + pad && y >= pad && y < h + pad
                            ? input_value(x - pad, y - pad, g * 8 + lane) : 0.f);
        }
        for (int y = 0; y < outh; y++)
            for (int x = 0; x < outw * 8; x++)
                out.row<__fp16>(y)[x] = static_cast<__fp16>(std::numeric_limits<float>::quiet_NaN());
        __fp16* kernel = weights.row<__fp16>(g);
        for (int k = 0; k < 9; k++)
            for (int lane = 0; lane < 8; lane++)
                kernel[k * 8 + lane] = static_cast<__fp16>(weight_value(k, g * 8 + lane));
    }
    if (with_bias)
    {
        __fp16* values = bias;
        for (int c = 0; c < channels; c++)
            values[c] = static_cast<__fp16>(bias_value(c));
    }

    ncnn::Option opt;
    opt.num_threads = 1;
    opt.use_packing_layout = true;
    opt.use_fp16_storage = true;
    opt.use_fp16_arithmetic = true;
    // No layer dispatcher, conversion helper, or fallback is involved here.
    ncnn::convdw3x3s2_pack8_fp16sa_neon(input, output, weights, bias, opt);

    int mismatches = 0;
    for (int c = 0; c < channels; c++)
    {
        const ncnn::Mat out = output.channel(c / 8);
        for (int y = 0; y < outh; y++)
            for (int x = 0; x < outw; x++)
            {
                float expected = with_bias ? bias_value(c) : 0.f;
                // Independent, unpadded scalar FP32 reference; do not read back
                // the packed input/weights, so layout mistakes cannot cancel.
                for (int ky = 0; ky < 3; ky++)
                    for (int kx = 0; kx < 3; kx++)
                    {
                        const int ix = x * 2 + kx - pad;
                        const int iy = y * 2 + ky - pad;
                        if (ix >= 0 && ix < w && iy >= 0 && iy < h)
                            expected += input_value(ix, iy, c) * weight_value(ky * 3 + kx, c);
                    }
                const float actual = static_cast<float>(out.row<const __fp16>(y)[x * 8 + c % 8]);
                checked_values++;
                // All products and partial sums are exact binary fractions
                // within FP16's exact range; no loose fp16-scaled tolerance.
                if (!std::isfinite(actual) || std::fabs(actual - expected) > 0.0001f)
                {
                    if (mismatches++ < 3)
                        std::fprintf(stderr, "NUMERICAL_MISMATCH w=%d h=%d pad=%d c=%d bias=%d x=%d y=%d channel=%d actual=%.9g expected=%.9g\n",
                                     w, h, pad, channels, with_bias, x, y, c, actual, expected);
                }
            }
    }
    return mismatches ? 2 : 0;
}
#endif

int main()
{
#if !defined(__aarch64__) || !defined(__ARM_FEATURE_FP16_VECTOR_ARITHMETIC)
    std::fprintf(stderr, "ERROR: AArch64 FP16 vector arithmetic build is required; no skip or fallback\n");
    return 1;
#else
    if (!ncnn::cpu_support_arm_asimdhp())
    {
        std::fprintf(stderr, "ERROR: AArch64 FP16 vector arithmetic hardware is required; no skip or fallback\n");
        return 1;
    }
    // Includes 28->14 and 14->7, four-column bulk, two-column and single-column
    // remainders, and both zero padding and no padding. Height also varies.
    const int shapes[][3] = {{28, 28, 1}, {14, 14, 1}, {5, 5, 1}, {12, 7, 1},
                            {15, 9, 0}, {9, 7, 0}, {13, 5, 0}, {7, 9, 0}};
    int result = 0;
    int cases = 0;
    unsigned long long checked_values = 0;
    for (int i = 0; i < 8; i++)
        for (int channels = 8; channels <= 16; channels += 8)
            for (int bias = 0; bias <= 1; bias++)
            {
                const int ret = run_case(shapes[i][0], shapes[i][1], shapes[i][2], channels, bias != 0, checked_values);
                if (ret == 1)
                    return 1;
                if (ret == 2)
                    result = 2;
                cases++;
            }
    if (result)
        return result;
    std::printf("PASS: %d direct ARM FP16 pack8 depthwise stride-2 cases, %llu scalar comparisons; no fallback\n", cases, checked_values);
    return 0;
#endif
}
