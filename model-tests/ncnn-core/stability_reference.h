// Independent, finite double references shared by the CPU and Vulkan tests.
// No ncnn reduction/activation implementation participates in the oracle.
#ifndef AGENT_STABILITY_REFERENCE_H
#define AGENT_STABILITY_REFERENCE_H

#include "mat.h"
#include "paramdict.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <limits>
#include <vector>

namespace stability {

struct ReductionCase
{
    const char* name;
    int dims, w, h, d, c;
    int axis_count, axes[2], keepdims;
    float coeff;
};

static const ReductionCase reduction_cases[] = {
    // 257 includes a second loop iteration and a tail in the 256-thread shader.
    {"all_1d_257", 1, 257, 1, 1, 1, 0, {0, 0}, 0, 1.f},
    {"width_2d", 2, 17, 3, 1, 1, 1, {-1, 0}, 0, 1.f},
    {"height_2d_keep", 2, 9, 5, 1, 1, 1, {0, 0}, 1, 0.5f},
    {"spatial_3d_keep", 3, 9, 5, 1, 3, 2, {-2, -1}, 1, 0.5f},
    {"channels_3d", 3, 9, 5, 1, 3, 1, {0, 0}, 0, -0.5f},
    {"depth_width_4d", 4, 5, 3, 2, 3, 2, {1, 3}, 0, 1.f},
    // Reducing c leaves a padded 3D output (w*h is odd), not a dense 2D map.
    {"channels_4d_padded", 4, 5, 3, 2, 3, 1, {0, 0}, 0, 1.f},
    {"all_4d_keep", 4, 5, 3, 2, 3, 0, {0, 0}, 1, 0.5f}
};

static int failure(const char* stage)
{
    std::fprintf(stderr, "INFRASTRUCTURE_FAILURE stability: %s (see RUN case above)\n", stage);
    return 1;
}

static int count(const ncnn::Mat& m) { return m.w * m.h * m.d * m.c; }

// Logical flat indexing excludes ncnn's channel alignment padding.
static float& value(ncnn::Mat& m, int i)
{
    const int size = m.w * m.h * m.d;
    return ((float*)m)[size_t(i / size) * m.cstep + i % size];
}

static float value(const ncnn::Mat& m, int i)
{
    const int size = m.w * m.h * m.d;
    return ((const float*)m)[size_t(i / size) * m.cstep + i % size];
}

static ncnn::Mat shape(const std::vector<int>& extents)
{
    if (extents.empty()) return ncnn::Mat(1);
    if (extents.size() == 1) return ncnn::Mat(extents[0]);
    if (extents.size() == 2) return ncnn::Mat(extents[1], extents[0]);
    if (extents.size() == 3) return ncnn::Mat(extents[2], extents[1], extents[0]);
    return ncnn::Mat(extents[3], extents[2], extents[1], extents[0]);
}

static std::vector<int> extents(const ReductionCase& tc)
{
    std::vector<int> result;
    if (tc.dims >= 3) result.push_back(tc.c);
    if (tc.dims == 4) result.push_back(tc.d);
    if (tc.dims >= 2) result.push_back(tc.h);
    result.push_back(tc.w);
    return result;
}

static ncnn::Mat reduction_input(const ReductionCase& tc, int pattern)
{
    ncnn::Mat input = shape(extents(tc));
    if (input.empty()) return input;
    input.fill(0.f);
    for (int i = 0; i < count(input); i++)
    {
        // All values are exactly representable in binary16. Positive inputs
        // overflow an unshifted FP32 exp; negative inputs underflow it to zero.
        // The mixed case varies the maximum within each reduced slice.
        const float base = pattern == 0 ? 1000.f : pattern == 1 ? -1000.f : (i % 3 ? 1000.f : -1000.f);
        value(input, i) = base + float((i * 7 + i / 11) % 13) * 0.5f;
    }
    return input;
}

static ncnn::ParamDict reduction_params(const ReductionCase& tc)
{
    ncnn::ParamDict pd;
    pd.set(0, 10); // Real ReductionOp_LogSumExp, not a precomputed identity.
    pd.set(1, tc.axis_count == 0 ? 1 : 0);
    pd.set(2, tc.coeff);
    if (tc.axis_count)
    {
        ncnn::Mat axes(tc.axis_count);
        for (int i = 0; i < tc.axis_count; i++) ((int*)axes)[i] = tc.axes[i];
        pd.set(3, axes);
    }
    pd.set(4, tc.keepdims);
    pd.set(5, 1);
    return pd;
}

struct Reference
{
    ncnn::Mat layout;
    std::vector<double> values;
};

static Reference reduction_reference(const ReductionCase& tc, const ncnn::Mat& input)
{
    const std::vector<int> in = extents(tc);
    std::vector<bool> reduced(tc.dims, tc.axis_count == 0);
    for (int i = 0; i < tc.axis_count; i++)
        reduced[tc.axes[i] < 0 ? tc.axes[i] + tc.dims : tc.axes[i]] = true;
    std::vector<int> out;
    for (int i = 0; i < tc.dims; i++)
        if (!reduced[i] || tc.keepdims) out.push_back(reduced[i] ? 1 : in[i]);
    Reference result;
    result.layout = shape(out);
    if (result.layout.empty()) return result;
    const int n = count(result.layout);
    std::vector<std::vector<double> > buckets(n);
    for (int i = 0; i < count(input); i++)
    {
        int coord[4] = {0, 0, 0, 0};
        int index = i;
        for (int a = tc.dims - 1; a >= 0; a--) { coord[a] = index % in[a]; index /= in[a]; }
        int target = 0;
        for (int a = 0; a < tc.dims; a++)
            if (!reduced[a]) target = target * in[a] + coord[a];
        buckets[target].push_back(double(value(input, i)));
    }
    for (int i = 0; i < n; i++)
    {
        const double maximum = *std::max_element(buckets[i].begin(), buckets[i].end());
        double sum = 0.;
        for (size_t j = 0; j < buckets[i].size(); j++) sum += std::exp(buckets[i][j] - maximum);
        result.values.push_back((maximum + std::log(sum)) * double(tc.coeff));
    }
    return result;
}

static bool same_layout(const ncnn::Mat& a, const ncnn::Mat& b)
{
    return !a.empty() && !b.empty() && a.dims == b.dims && a.w == b.w && a.h == b.h
        && a.d == b.d && a.c == b.c && a.elempack == 1 && a.elembits() == 32;
}

static int compare(const char* name, const ncnn::Mat& actual, const Reference& expected, bool half)
{
    if (!same_layout(actual, expected.layout) || int(expected.values.size()) != count(actual))
        return failure("download/reference shape or FP32 pack1 layout");
    int mismatches = 0, nonfinite = 0;
    double maximum = 0.;
    for (int i = 0; i < count(actual); i++)
    {
        const double e = expected.values[i];
        const float a = value(actual, i);
        if (!std::isfinite(e)) return failure("nonfinite independent reference");
        // Half of one binary16 ulp plus a small arithmetic allowance. Absolute
        // FP32 error near 1000 stays below 0.00025, not a percent-wide budget.
        const double tolerance = half ? 0.003 + 0.0005 * std::fabs(e) : 0.00005 + 0.0000002 * std::fabs(e);
        const double error = std::isfinite(a) ? std::fabs(double(a) - e) : std::numeric_limits<double>::infinity();
        maximum = std::max(maximum, error);
        if (!std::isfinite(a) || error > tolerance)
        {
            if (!mismatches) std::fprintf(stderr, "NUMERICAL_MISMATCH op=%s index=%d expected=%.17g actual=%.9g tolerance=%.9g\n", name, i, e, a, tolerance);
            mismatches++;
            nonfinite += !std::isfinite(a);
        }
    }
    std::fprintf(stderr, "%s op=%s max_abs_error=%.9g mismatches=%d nonfinite=%d\n", mismatches ? "FAIL" : "PASS", name, maximum, mismatches, nonfinite);
    return mismatches ? 2 : 0;
}

} // namespace stability
#endif
