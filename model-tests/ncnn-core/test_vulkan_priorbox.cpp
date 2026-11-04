// Strict Caffe-style PriorBox FP16 overflow regression. This executes the real
// PriorBox_vulkan pipeline, never Net's CPU fallback or an upstream GPU skip.
// Exit 1: missing capability, pipeline, submission or layout failure.
// Exit 2 + NUMERICAL_MISMATCH: a submitted result disagrees with the reference.
#include "allocator.h"
#include "command.h"
#include "gpu.h"
#include "pipeline.h"
#include "priorbox_vulkan.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#if !NCNN_VULKAN
#error This regression requires NCNN_VULKAN
#endif

struct Case
{
    const char* name;
    int w, h, image_w, image_h;
    float min_size, max_size, step_w, step_h;
    bool image_from_blob;
};

static const float kOffset = 0.5f;
static const float kRatios[] = {2.f, 3.f};
static const float kVariances[] = {0.1f, 0.1f, 0.2f, 0.2f};
static const float kHalfTolerance = 0.0025f;

static int failure(const char* stage)
{
    fprintf(stderr, "INFRASTRUCTURE_FAILURE PriorBox: %s (see RUN case above)\n", stage);
    return 1;
}

struct DestroyPipeline
{
    ncnn::PriorBox_vulkan& op;
    const ncnn::Option& opt;
    DestroyPipeline(ncnn::PriorBox_vulkan& layer, const ncnn::Option& option) : op(layer), opt(option) {}
    ~DestroyPipeline() { op.destroy_pipeline(opt); }
};

static bool scalar_layout(const ncnn::VkMat& m, int bits)
{
    return !m.empty() && m.elempack == 1 && m.elembits() == bits;
}

// Independent FP32 geometry, with no calls to a CPU PriorBox implementation,
// test_layer helper, half arithmetic or the Vulkan shader under test.
static void reference_box(float* dst, float cx, float cy, float bw, float bh,
                          const Case& tc, bool clip)
{
    dst[0] = (cx - 0.5f * bw) / float(tc.image_w);
    dst[1] = (cy - 0.5f * bh) / float(tc.image_h);
    dst[2] = (cx + 0.5f * bw) / float(tc.image_w);
    dst[3] = (cy + 0.5f * bh) / float(tc.image_h);
    if (clip)
        for (int i = 0; i < 4; i++)
            dst[i] = std::max(0.f, std::min(1.f, dst[i]));
}

static ncnn::Mat reference(const Case& tc, bool flip, bool clip)
{
    const int priors = 2 + 2 * (flip ? 2 : 1);
    ncnn::Mat result(4 * tc.w * tc.h * priors, 2);
    if (result.empty()) return result;
    float* boxes = result.row(0);
    float* variances = result.row(1);
    const float sw = tc.step_w == -233.f ? float(tc.image_w) / tc.w : tc.step_w;
    const float sh = tc.step_h == -233.f ? float(tc.image_h) / tc.h : tc.step_h;
    for (int y = 0; y < tc.h; y++)
        for (int x = 0; x < tc.w; x++)
        {
            const float cx = (float(x) + kOffset) * sw;
            const float cy = (float(y) + kOffset) * sh;
            const int first = (y * tc.w + x) * priors;
            float* box = boxes + 4 * first;
            reference_box(box, cx, cy, tc.min_size, tc.min_size, tc, clip);
            box += 4;
            // The product must be evaluated in FP32 before taking the root.
            const float side = std::sqrt(tc.min_size * tc.max_size);
            reference_box(box, cx, cy, side, side, tc, clip);
            box += 4;
            for (int r = 0; r < 2; r++)
            {
                const float ratio_root = std::sqrt(kRatios[r]);
                const float bw = tc.min_size * ratio_root;
                const float bh = tc.min_size / ratio_root;
                reference_box(box, cx, cy, bw, bh, tc, clip);
                box += 4;
                if (flip)
                {
                    reference_box(box, cx, cy, bh, bw, tc, clip);
                    box += 4;
                }
            }
            for (int p = 0; p < priors; p++)
                for (int i = 0; i < 4; i++)
                    variances[4 * (first + p) + i] = kVariances[i];
        }
    return result;
}

static int run_case(ncnn::VulkanDevice* device, const Case& tc, bool half, bool flip, bool clip)
{
    fprintf(stderr, "RUN PriorBox case=%s feature=%dx%d image=%dx%d min=%.9g max=%.9g fp16_storage_arithmetic=%d flip=%d clip=%d\n",
            tc.name, tc.w, tc.h, tc.image_w, tc.image_h, tc.min_size, tc.max_size, half, flip, clip);
    const int bits = half ? 16 : 32;
    const int priors = 2 + 2 * (flip ? 2 : 1);
    const int output_width = 4 * tc.w * tc.h * priors;
    const ncnn::Mat expected = reference(tc, flip, clip);
    if (expected.empty()) return failure("reference allocation");

    // The old shader's clipped infinities become finite [0, 0, 1, 1]. Ensure
    // every overflowing case has an interior max-size coordinate far enough
    // from those endpoints to detect that error even when clip is enabled.
    if (tc.min_size * tc.max_size > 65504.f)
    {
        const int center = ((tc.h / 2) * tc.w + tc.w / 2) * priors + 1;
        const float* max_box = expected.row(0) + 4 * center;
        bool witness = false;
        for (int i = 0; i < 4; i++)
            witness = witness || (max_box[i] > 4.f * kHalfTolerance && max_box[i] < 1.f - 4.f * kHalfTolerance);
        if (!witness) return failure("overflow fixture lacks an unclipped numerical witness");
    }

    ncnn::VkBlobAllocator blob_allocator(device);
    ncnn::VkStagingAllocator staging_allocator(device);
    ncnn::VkWeightAllocator weight_allocator(device);
    ncnn::Option opt;
    opt.num_threads = 1;
    opt.use_vulkan_compute = true;
    opt.use_packing_layout = false;
    opt.use_fp16_uniform = false;
    opt.use_fp16_packed = false;
    opt.use_fp16_storage = opt.use_fp16_arithmetic = half;
    opt.use_bf16_packed = opt.use_bf16_storage = false;
    opt.use_int8_packed = opt.use_int8_storage = opt.use_int8_arithmetic = false;
    opt.use_subgroup_ops = false;
    opt.use_cooperative_matrix = false;
    opt.blob_vkallocator = opt.workspace_vkallocator = &blob_allocator;
    opt.staging_vkallocator = &staging_allocator;

    ncnn::Mat min_sizes(1), max_sizes(1), ratios(2);
    if (min_sizes.empty() || max_sizes.empty() || ratios.empty()) return failure("parameter allocation");
    min_sizes[0] = tc.min_size;
    max_sizes[0] = tc.max_size;
    ratios[0] = kRatios[0];
    ratios[1] = kRatios[1];
    ncnn::ParamDict pd;
    pd.set(0, min_sizes);
    pd.set(1, max_sizes);
    pd.set(2, ratios);
    for (int i = 0; i < 4; i++) pd.set(3 + i, kVariances[i]);
    pd.set(7, int(flip));
    pd.set(8, int(clip));
    pd.set(9, tc.image_from_blob ? -233 : tc.image_w);
    pd.set(10, tc.image_from_blob ? -233 : tc.image_h);
    pd.set(11, tc.step_w);
    pd.set(12, tc.step_h);
    pd.set(13, kOffset);

    ncnn::PriorBox_vulkan op;
    op.vkdev = device;
    DestroyPipeline cleanup(op, opt);
    if (op.load_param(pd) || !op.support_vulkan || op.create_pipeline(opt)) return failure("load/create pipeline");
    const ncnn::Pipeline* pipeline = op.pipeline_priorbox;
    if (!pipeline || pipeline->pipeline() == VK_NULL_HANDLE || !pipeline->local_size_x()
        || !pipeline->local_size_y() || !pipeline->local_size_z()) return failure("invalid Caffe PriorBox pipeline");

    // PriorBox parameters are model buffers, not specialization constants for
    // their values. Require their actual upload and scalar FP16 storage.
    uint64_t upload_bytes = 0;
    {
        ncnn::Option weight_opt = opt;
        weight_opt.blob_vkallocator = &weight_allocator;
        ncnn::VkTransfer transfer(device);
        if (op.upload_model(transfer, weight_opt)) return failure("record model upload");
        upload_bytes = transfer.pending_upload_total();
        if (!upload_bytes || transfer.submit_and_wait()) return failure("submit model upload");
    }
    if (!scalar_layout(op.min_sizes_gpu, bits) || op.min_sizes_gpu.dims != 1 || op.min_sizes_gpu.w != 1
        || !scalar_layout(op.max_sizes_gpu, bits) || op.max_sizes_gpu.dims != 1 || op.max_sizes_gpu.w != 1
        || !scalar_layout(op.aspect_ratios_gpu, bits) || op.aspect_ratios_gpu.dims != 1 || op.aspect_ratios_gpu.w != 2)
        return failure("uploaded model parameter shape/storage");

    // PriorBox only uses these tensors' dimensions; their contents are neither
    // read nor bound by the shader. Allocate real VkMats, retaining pack1 shape.
    std::vector<ncnn::VkMat> inputs(tc.image_from_blob ? 2 : 1), outputs(1);
    inputs[0].create(tc.w, tc.h, 1, size_t(bits / 8), 1, &blob_allocator);
    if (!scalar_layout(inputs[0], bits) || inputs[0].dims != 3) return failure("feature shape allocation");
    if (tc.image_from_blob)
    {
        inputs[1].create(tc.image_w, tc.image_h, 1, size_t(bits / 8), 1, &blob_allocator);
        if (!scalar_layout(inputs[1], bits) || inputs[1].dims != 3) return failure("image shape allocation");
    }

    ncnn::Mat stored;
    uint64_t dispatches = 0;
    {
        ncnn::VkCompute cmd(device);
        const uint64_t before = cmd.pending_dispatch_total();
        if (op.forward(inputs, outputs, cmd, opt)) return failure("record forward");
        dispatches = cmd.pending_dispatch_total() - before;
        // The shader dispatch dimensions are (num_min_sizes, feature_w,
        // feature_h), not the dimensions of its flattened coordinate output.
        const uint64_t required = uint64_t((1 + pipeline->local_size_x() - 1) / pipeline->local_size_x())
            * ((tc.w + pipeline->local_size_y() - 1) / pipeline->local_size_y())
            * ((tc.h + pipeline->local_size_z() - 1) / pipeline->local_size_z());
        if (!dispatches || dispatches != required) return failure("PriorBox dispatch workgroup count");
        if (!scalar_layout(outputs[0], bits) || outputs[0].dims != 2 || outputs[0].w != output_width
            || outputs[0].h != 2 || outputs[0].c != 1) return failure("GPU coordinate/variance layout");
        // Clone raw storage, then convert on the CPU after submission. This
        // avoids introducing another shader or a packing conversion to readback.
        cmd.record_clone(outputs[0], stored, opt);
        if (cmd.pending_dispatch_total() - before != dispatches) return failure("unexpected readback dispatch");
        if (cmd.submit_and_wait()) return failure("submit PriorBox and readback");
    }
    if (stored.empty() || stored.dims != 2 || stored.w != output_width || stored.h != 2 || stored.c != 1
        || stored.elempack != 1 || stored.elembits() != bits) return failure("raw download layout");
    ncnn::Mat actual = stored;
    if (half) ncnn::cast_float16_to_float32(stored, actual, opt);
    if (actual.empty() || actual.dims != 2 || actual.w != output_width || actual.h != 2
        || actual.c != 1 || actual.elempack != 1 || actual.elembits() != 32) return failure("FP32 readback conversion");

    // Absolute normalized-coordinate tolerance bounds half arithmetic and
    // storage rounding. Even the smallest overflow witness (285*300, clip=1)
    // differs from the old saturated result by about 0.01266, over 5x this bound.
    const float tolerance = half ? kHalfTolerance : 0.000003f;
    float maximum = 0.f;
    int mismatches = 0, max_size_mismatches = 0, nonfinite = 0;
    for (int row = 0; row < 2; row++)
    {
        const float* a = actual.row(row);
        const float* e = expected.row(row);
        for (int i = 0; i < output_width; i++)
        {
            if (!std::isfinite(e[i])) return failure("nonfinite independent reference");
            const bool finite = std::isfinite(a[i]);
            const float error = finite ? std::fabs(a[i] - e[i]) : INFINITY;
            maximum = std::max(maximum, error);
            if (!finite || error > tolerance)
            {
                if (!mismatches)
                    fprintf(stderr, "NUMERICAL_MISMATCH case=%s fp16=%d flip=%d clip=%d row=%d index=%d expected=%.9g actual=%.9g tolerance=%.9g\n",
                            tc.name, half, flip, clip, row, i, e[i], a[i], tolerance);
                mismatches++;
                nonfinite += !finite;
                max_size_mismatches += row == 0 && (i / 4) % priors == 1;
            }
        }
    }
    fprintf(stderr, "%s PriorBox case=%s fp16_storage_arithmetic=%d flip=%d clip=%d upload_bytes=%llu dispatch_groups=%llu max_abs_error=%.9g mismatches=%d max_size_mismatches=%d nonfinite=%d\n",
            mismatches ? "FAIL" : "PASS", tc.name, half, flip, clip, (unsigned long long)upload_bytes,
            (unsigned long long)dispatches, maximum, mismatches, max_size_mismatches, nonfinite);
    return mismatches ? 2 : 0;
}

static int run_all(ncnn::VulkanDevice* device)
{
    if (!device) return failure("missing Vulkan device");
    const ncnn::GpuInfo& info = device->info;
    fprintf(stderr, "Vulkan device=%s driver=%s fp16_storage=%d fp16_arithmetic=%d\n",
            info.device_name(), info.driver_name(), info.support_fp16_storage(), info.support_fp16_arithmetic());
    if (!info.support_fp16_storage() || !info.support_fp16_arithmetic())
        return failure("FP16 storage and arithmetic are mandatory; no skip or CPU fallback");
    if (std::getenv("AGENT_REQUIRE_LAVAPIPE") && (info.type() != 3
        || (!std::strstr(info.device_name(), "llvmpipe") && !std::strstr(info.device_name(), "lavapipe"))))
        return failure("CI must select Mesa lavapipe software Vulkan");

    const Case cases[] = {
        // Conventional SSD300 scales/feature maps/steps. The final level's
        // 264*315 product overflows binary16 although its square root does not.
        {"ssd300_38", 38, 38, 300, 300, 30.f, 60.f, 8.f, 8.f, false},
        {"ssd300_19", 19, 19, 300, 300, 60.f, 111.f, 16.f, 16.f, false},
        {"ssd300_10", 10, 10, 300, 300, 111.f, 162.f, 32.f, 32.f, false},
        {"ssd300_5", 5, 5, 300, 300, 162.f, 213.f, 64.f, 64.f, false},
        {"ssd300_3", 3, 3, 300, 300, 213.f, 264.f, 100.f, 100.f, false},
        {"ssd300_1_overflow", 1, 1, 300, 300, 264.f, 315.f, 300.f, 300.f, false},
        {"285x300_overflow", 1, 1, 300, 300, 285.f, 300.f, -233.f, -233.f, false},
        {"below_fp16_limit", 1, 1, 300, 300, 255.f, 256.f, -233.f, -233.f, false},
        {"above_fp16_limit", 1, 1, 300, 300, 256.f, 257.f, -233.f, -233.f, false},
        // Non-square normalization, inferred image dimensions, automatic steps,
        // and edge boxes which exercise real clipping in both axes.
        {"rectangular_overflow", 5, 3, 384, 320, 264.f, 315.f, -233.f, -233.f, true}
    };
    int submitted = 0, numerical_failures = 0;
    for (int half = 0; half < 2; half++)
        for (int flip = 0; flip < 2; flip++)
            for (int clip = 0; clip < 2; clip++)
                for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
                {
                    const int result = run_case(device, cases[i], half != 0, flip != 0, clip != 0);
                    if (result == 1) return 1;
                    numerical_failures += result == 2;
                    submitted++;
                }
    fprintf(stderr, "%s: %d submitted PriorBox cases; numerical_failures=%d; software Vulkan is not physical GPU/model validation\n",
            numerical_failures ? "FAIL" : "PASS", submitted, numerical_failures);
    return numerical_failures ? 2 : 0;
}

int main()
{
    if (ncnn::create_gpu_instance() != 0)
        return failure("a working Vulkan device is required; cannot create Vulkan instance");
    const int result = ncnn::get_gpu_count() == 0
        ? failure("a working Vulkan device is required; no skipped success")
        : run_all(ncnn::get_gpu_device());
    ncnn::destroy_gpu_instance();
    return result;
}
