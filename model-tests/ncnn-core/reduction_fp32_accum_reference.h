// Finite adversarial fixtures for Reduction's FP32 accumulator, independent of
// the ncnn CPU/Vulkan implementations. This is not a GPU test by itself.
#ifndef AGENT_REDUCTION_FP32_ACCUM_REFERENCE_H
#define AGENT_REDUCTION_FP32_ACCUM_REFERENCE_H

#include "stability_reference.h"

namespace accumulation {

static const int operations[] = {0, 1, 2, 3, 7, 8, 9};

static const stability::ReductionCase cases[] = {
    // More than 16 elements per invocation: even a thread-local FP16 sum can
    // overflow. Other shapes also exercise cross-lane sums, tails and padding.
    {"all_1d_4097", 1, 4097, 1, 1, 1, 0, {0, 0}, 0, 1.f},
    {"width_2d_257", 2, 257, 3, 1, 1, 1, {-1, 0}, 0, 1.f},
    {"height_2d_keep_257", 2, 5, 257, 1, 1, 1, {0, 0}, 1, 0.5f},
    {"spatial_3d_keep_289", 3, 17, 17, 1, 3, 2, {-2, -1}, 1, 0.5f},
    {"channels_3d_257", 3, 5, 3, 1, 257, 1, {0, 0}, 0, -0.5f},
    {"depth_width_4d_289", 4, 17, 3, 17, 3, 2, {1, 3}, 0, 1.f},
    {"channels_4d_padded_257", 4, 5, 3, 3, 257, 1, {0, 0}, 0, 1.f},
    {"all_4d_keep_459", 4, 17, 3, 3, 3, 0, {0, 0}, 1, 0.5f}
};

static const char* operation_name(int op)
{
    switch (op)
    {
    case 0: return "sum";
    case 1: return "asum";
    case 2: return "sumsq";
    case 3: return "mean";
    case 7: return "l1";
    case 8: return "l2";
    case 9: return "logsum";
    default: return "invalid";
    }
}

static float coefficient(const stability::ReductionCase& tc, int op)
{
    // Sum-family outputs must fit binary16 even though the accumulator does
    // not. Scaling happens after reduction, exactly where the old shader lost
    // finiteness. Mean, sqrt and log naturally restore a representable range.
    return tc.coeff * (op == 2 ? 1.f / 32768.f : (op == 0 || op == 1 || op == 7) ? 1.f / 4096.f : 1.f);
}

static ncnn::Mat input(const stability::ReductionCase& tc, int op, int pattern)
{
    ncnn::Mat result = stability::shape(stability::extents(tc));
    if (result.empty()) return result;
    result.fill(0.f);
    const float base = op == 2 ? 128.f : op == 8 ? 256.f : 8192.f;
    for (int i = 0; i < stability::count(result); i++)
    {
        // Small powers of two are exactly representable in binary16. Mixed
        // signs distinguish SUM/MEAN from ASUM/L1 without making LogSum invalid.
        const float magnitude = base * (1 + i % 2);
        stability::value(result, i) = pattern == 1 && i % 4 == 0 ? -magnitude : magnitude;
    }
    return result;
}

struct Reference
{
    stability::Reference expected;
    std::vector<double> accumulators;
    std::vector<int> sizes;
};

static Reference reference(const stability::ReductionCase& tc, const ncnn::Mat& input, int op)
{
    const std::vector<int> in = stability::extents(tc);
    std::vector<bool> reduced(tc.dims, tc.axis_count == 0);
    for (int i = 0; i < tc.axis_count; i++)
        reduced[tc.axes[i] < 0 ? tc.axes[i] + tc.dims : tc.axes[i]] = true;
    std::vector<int> out;
    for (int i = 0; i < tc.dims; i++)
        if (!reduced[i] || tc.keepdims) out.push_back(reduced[i] ? 1 : in[i]);
    Reference result;
    result.expected.layout = stability::shape(out);
    if (result.expected.layout.empty()) return result;
    const int n = stability::count(result.expected.layout);
    result.accumulators.assign(n, 0.);
    result.sizes.assign(n, 0);
    for (int i = 0; i < stability::count(input); i++)
    {
        int coord[4] = {0, 0, 0, 0};
        int index = i;
        for (int a = tc.dims - 1; a >= 0; a--) { coord[a] = index % in[a]; index /= in[a]; }
        int target = 0;
        for (int a = 0; a < tc.dims; a++)
            if (!reduced[a]) target = target * in[a] + coord[a];
        const double x = stability::value(input, i);
        result.accumulators[target] += op == 1 || op == 7 ? std::fabs(x) : op == 2 || op == 8 ? x * x : x;
        result.sizes[target]++;
    }
    for (int i = 0; i < n; i++)
    {
        double x = result.accumulators[i];
        if (op == 3) x /= result.sizes[i];
        if (op == 8) x = std::sqrt(x);
        if (op == 9) x = std::log(x);
        result.expected.values.push_back(x * double(coefficient(tc, op)));
    }
    return result;
}

static bool valid_fixture(const ncnn::Mat& input, const Reference& expected)
{
    if (input.empty() || input.elembits() != 32 || input.elempack != 1 || expected.expected.layout.empty()) return false;
    const size_t n = size_t(stability::count(expected.expected.layout));
    if (expected.expected.values.size() != n || expected.accumulators.size() != n || expected.sizes.size() != n) return false;
    int elements = 0;
    for (int i = 0; i < stability::count(input); i++)
    {
        const double x = stability::value(input, i);
        if (!std::isfinite(x) || std::fabs(x) > 65504.) return false;
    }
    for (size_t i = 0; i < n; i++)
    {
        // Require an actual adversarial reduction in EVERY output slice, not
        // just a large tensor whose individual reductions stay within FP16.
        if (expected.sizes[i] <= 256 || !std::isfinite(expected.accumulators[i])
            || expected.accumulators[i] <= 65504. || !std::isfinite(expected.expected.values[i])
            || std::fabs(expected.expected.values[i]) > 65504.) return false;
        elements += expected.sizes[i];
    }
    return elements == stability::count(input);
}

static bool exact_storage(const ncnn::Mat& input, bool half, ncnn::Mat& storage, const ncnn::Option& cpu_opt)
{
    storage = input;
    if (half) ncnn::cast_float32_to_float16(input, storage, cpu_opt);
    if (storage.empty() || storage.elembits() != (half ? 16 : 32) || storage.elempack != 1) return false;
    ncnn::Mat roundtrip = storage;
    if (half) ncnn::cast_float16_to_float32(storage, roundtrip, cpu_opt);
    if (!stability::same_layout(roundtrip, input)) return false;
    for (int i = 0; i < stability::count(input); i++)
        if (!std::isfinite(stability::value(roundtrip, i)) || stability::value(roundtrip, i) != stability::value(input, i)) return false;
    return true;
}

} // namespace accumulation
#endif
