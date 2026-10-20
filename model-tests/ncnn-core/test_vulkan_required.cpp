// A strict companion to the unchanged upstream tests: no device/233 skips and
// no Net CPU fallback. Vulkan on lavapipe is software execution, not hardware
// GPU validation or trained-model inference.
#include "testutil.h"
#include "command.h"
#include "gpu.h"
#include "pipeline.h"
#include "layernorm_vulkan.h"
#include "rmsnorm_vulkan.h"
#include "unaryop_vulkan.h"
#include "groupnorm_vulkan.h"
#include "instancenorm_vulkan.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>

#if !NCNN_VULKAN
#error This regression must be built with NCNN_VULKAN enabled
#endif

static bool valid(const ncnn::Pipeline* p)
{
    return p && p->pipeline() != VK_NULL_HANDLE;
}

// Keep the stage checks aligned with the pinned Group/Instance implementations.
struct SpatialNormPipelines
{
    const ncnn::Pipeline* subgroup;
    const ncnn::Pipeline* first;
    const ncnn::Pipeline* sum[2];
    const ncnn::Pipeline* mean;
    const ncnn::Pipeline* square;
    const ncnn::Pipeline* coeffs;
    const ncnn::Pipeline* norm;
};

static SpatialNormPipelines spatial_pipelines(ncnn::Layer* op, const char* name, int pack)
{
    SpatialNormPipelines stages;
    if (std::strcmp(name, "GroupNorm") == 0)
    {
        const ncnn::GroupNorm_vulkan* p = static_cast<ncnn::GroupNorm_vulkan*>(op);
        stages.subgroup = pack == 4 ? p->pipeline_groupnorm_reduce_subgroup_pack4 : p->pipeline_groupnorm_reduce_subgroup;
        stages.first = pack == 4 ? p->pipeline_groupnorm_reduce_sum4_fp16_to_fp32_pack4 : p->pipeline_groupnorm_reduce_sum4_fp16_to_fp32;
        for (int i = 0; i < 2; i++)
            stages.sum[i] = pack == 4 ? p->pipeline_groupnorm_reduce_sum4_fp32_pack4[i] : p->pipeline_groupnorm_reduce_sum4_fp32[i];
        stages.mean = pack == 4 ? p->pipeline_groupnorm_reduce_mean_pack4 : p->pipeline_groupnorm_reduce_mean;
        stages.square = pack == 4 ? p->pipeline_groupnorm_sub_mean_square_pack4 : p->pipeline_groupnorm_sub_mean_square;
        stages.coeffs = pack == 4 ? p->pipeline_groupnorm_coeffs_pack4 : p->pipeline_groupnorm_coeffs;
        stages.norm = pack == 4 ? p->pipeline_groupnorm_norm_pack4 : p->pipeline_groupnorm_norm;
    }
    else
    {
        const ncnn::InstanceNorm_vulkan* p = static_cast<ncnn::InstanceNorm_vulkan*>(op);
        stages.subgroup = pack == 4 ? p->pipeline_instancenorm_reduce_subgroup_pack4 : p->pipeline_instancenorm_reduce_subgroup;
        stages.first = pack == 4 ? p->pipeline_instancenorm_reduce_sum4_fp16_to_fp32_pack4 : p->pipeline_instancenorm_reduce_sum4_fp16_to_fp32;
        for (int i = 0; i < 2; i++)
            stages.sum[i] = pack == 4 ? p->pipeline_instancenorm_reduce_sum4_fp32_pack4[i] : p->pipeline_instancenorm_reduce_sum4_fp32[i];
        stages.mean = pack == 4 ? p->pipeline_instancenorm_reduce_mean_pack4 : p->pipeline_instancenorm_reduce_mean;
        stages.square = pack == 4 ? p->pipeline_instancenorm_sub_mean_square_pack4 : p->pipeline_instancenorm_sub_mean_square;
        stages.coeffs = pack == 4 ? p->pipeline_instancenorm_coeffs_pack4 : p->pipeline_instancenorm_coeffs;
        stages.norm = pack == 4 ? p->pipeline_instancenorm_norm_pack4 : p->pipeline_instancenorm_norm;
    }
    return stages;
}

static uint64_t workgroups(const ncnn::Pipeline* p, int w, int h, int c)
{
    return uint64_t((w + p->local_size_x() - 1) / p->local_size_x())
           * ((h + p->local_size_y() - 1) / p->local_size_y())
           * ((c + p->local_size_z() - 1) / p->local_size_z());
}

// pending_dispatch_total counts workgroups, not vkCmdDispatch calls. Predict
// every operator stage, excluding upload/download, so a missing reduction
// cannot pass merely because the coefficient/normalization stages ran.
static uint64_t spatial_dispatches(ncnn::Layer* op, const char* name, const ncnn::VkMat& a, int groups, bool subgroup)
{
    const bool groupnorm = std::strcmp(name, "GroupNorm") == 0;
    const SpatialNormPipelines p = spatial_pipelines(op, name, a.elempack);
    const int c = a.dims == 1 ? a.w : a.dims == 2 ? a.h : a.c;
    const int w = a.dims == 1 ? 1 : a.w;
    const int h = a.dims < 3 ? 1 : a.h * a.d;
    const int size = w * h;
    uint64_t total = workgroups(p.coeffs, c, 1, 1) + workgroups(p.norm, w, h, c);
    if (subgroup)
    {
        const int count = groupnorm ? groups : c;
        const int x = (count - 1) / op->vkdev->info.max_workgroup_count_y() + 1;
        const int y = (count - 1) / x + 1;
        return total + uint64_t(x) * y;
    }
    int reduced = (size + 3) / 4;
    total += workgroups(p.first, reduced, 1, c);
    for (int pass = 0; reduced > 4; pass++)
    {
        reduced = (reduced + 3) / 4;
        total += workgroups(p.sum[pass % 2], reduced, 1, c);
    }
    // GroupNorm has one scalar per group; InstanceNorm has packed channels.
    total += 2 * workgroups(p.mean, groupnorm ? groups : c, 1, 1);
    total += workgroups(p.square, w, h, c);
    reduced = size;
    int pass = 0;
    // InstanceNorm always performs the first variance reduction, even at <=4.
    if (!groupnorm)
    {
        reduced = (reduced + 3) / 4;
        total += workgroups(p.sum[pass++], reduced, 1, c);
    }
    while (reduced > 4)
    {
        reduced = (reduced + 3) / 4;
        total += workgroups(p.sum[pass++ % 2], reduced, 1, c);
    }
    return total;
}

static int channel_count(const ncnn::Mat& a)
{
    return a.dims == 1 ? a.w : a.dims == 2 ? a.h : a.c;
}

// An independent centered, two-pass double reference avoids the serial-float
// CPU baseline losing the same small deviations that these shaders must retain.
static ncnn::Mat spatial_reference(const ncnn::Mat& a, int groups, float eps)
{
    const int channels_g = channel_count(a) / groups;
    const int size = a.dims == 1 ? 1 : a.dims == 2 ? a.w : a.w * a.h * a.d;
    ncnn::Mat expected = a.clone();
    for (int g = 0; g < groups; g++)
    {
        const float* first = a.dims < 3 ? (const float*)a + g * channels_g * size : a.channel(g * channels_g);
        const double center = first[0];
        double sum = 0.0;
        for (int q = g * channels_g; q < (g + 1) * channels_g; q++)
        {
            const float* ptr = a.dims < 3 ? (const float*)a + q * size : a.channel(q);
            for (int i = 0; i < size; i++) sum += double(ptr[i]) - center;
        }
        const double mean = center + sum / (channels_g * size);
        double squares = 0.0;
        for (int q = g * channels_g; q < (g + 1) * channels_g; q++)
        {
            const float* ptr = a.dims < 3 ? (const float*)a + q * size : a.channel(q);
            for (int i = 0; i < size; i++)
            {
                const double d = double(ptr[i]) - mean;
                squares += d * d;
            }
        }
        const double scale = 1.0 / std::sqrt(squares / (channels_g * size) + eps);
        for (int q = g * channels_g; q < (g + 1) * channels_g; q++)
        {
            const float* ptr = a.dims < 3 ? (const float*)a + q * size : a.channel(q);
            float* out = a.dims < 3 ? (float*)expected + q * size : expected.channel(q);
            for (int i = 0; i < size; i++) out[i] = float((double(ptr[i]) - mean) * scale);
        }
    }
    return expected;
}

// Inspect the pinned implementation's public pipeline members. Merely setting
// use_subgroup_ops=true is insufficient evidence that the subgroup path ran.
static bool selected_pipeline(ncnn::Layer* op, const char* name, bool subgroup, int pack)
{
    if (std::strcmp(name, "UnaryOp") == 0)
        return valid(static_cast<ncnn::UnaryOp_vulkan*>(op)->pipeline_unaryop);
    if (std::strcmp(name, "LayerNorm") == 0)
    {
        const ncnn::LayerNorm_vulkan* p = static_cast<ncnn::LayerNorm_vulkan*>(op);
        const ncnn::Pipeline* reduce = pack == 4 ? p->pipeline_layernorm_reduce_subgroup_pack4 : p->pipeline_layernorm_reduce_subgroup;
        const ncnn::Pipeline* fallback = pack == 4 ? p->pipeline_layernorm_reduce_sum4_fp16_to_fp32_pack4 : p->pipeline_layernorm_reduce_sum4_fp16_to_fp32;
        return subgroup ? valid(reduce) : !reduce && valid(fallback);
    }
    if (std::strcmp(name, "GroupNorm") == 0 || std::strcmp(name, "InstanceNorm") == 0)
    {
        const SpatialNormPipelines p = spatial_pipelines(op, name, pack);
        const bool reduction = subgroup ? valid(p.subgroup) && !p.first
            : !p.subgroup && valid(p.first) && valid(p.sum[0]) && valid(p.sum[1]) && valid(p.mean) && valid(p.square);
        return reduction && valid(p.coeffs) && valid(p.norm);
    }
    if (std::strcmp(name, "RMSNorm") != 0) return false;
    const ncnn::RMSNorm_vulkan* p = static_cast<ncnn::RMSNorm_vulkan*>(op);
    const ncnn::Pipeline* reduce = pack == 4 ? p->pipeline_rmsnorm_reduce_subgroup_pack4 : p->pipeline_rmsnorm_reduce_subgroup;
    const ncnn::Pipeline* fallback = pack == 4 ? p->pipeline_rmsnorm_reduce_sum4_fp16_to_fp32_pack4 : p->pipeline_rmsnorm_reduce_sum4_fp16_to_fp32;
    return subgroup ? valid(reduce) : !reduce && valid(fallback);
}

static int run_case(ncnn::VulkanDevice* device, const char* name, const ncnn::Mat& input,
                    ncnn::Option opt, bool subgroup, int groups = 1,
                    const char* pattern = "baseline", float tolerance = 0.002f, bool direct_clone = false)
{
    const bool groupnorm = std::strcmp(name, "GroupNorm") == 0;
    const bool spatial = groupnorm || std::strcmp(name, "InstanceNorm") == 0;
    const int channels = channel_count(input);
    if (spatial && !groupnorm) groups = channels;
    const float eps = spatial ? 1.f / 1024 : 0.001f;
    const bool unary = std::strcmp(name, "UnaryOp") == 0;
    ncnn::ParamDict pd;
    pd.set(0, unary ? 18 : spatial ? (groupnorm ? groups : channels) : input.w);
    if (groupnorm)
    {
        pd.set(1, channels);
        pd.set(2, eps);
        pd.set(3, 0);
    }
    else if (!unary)
    {
        pd.set(1, eps);
        pd.set(2, 0); // no learned weights; upstream tests also cover affine cases
    }
    ncnn::Mat expected;
    if (unary)
    {
        expected = input.clone();
        const float rounded[] = {0.f, 2.f, 2.f, 4.f, 0.f, -2.f, -2.f, -4.f};
        for (int q = 0; q < input.c; q++)
        {
            float* p = expected.channel(q);
            for (int i = 0; i < input.w * input.h; i++) p[i] = rounded[i % 8];
        }
    }
    else if (spatial)
        expected = spatial_reference(input, groups, eps);
    else if (test_layer_naive(ncnn::layer_to_index(name), pd, std::vector<ncnn::Mat>(), input, expected, 0) != 0)
        return 1;

    ncnn::Layer* op = ncnn::create_layer_vulkan(name);
    if (!op || !op->support_vulkan)
    {
        delete op;
        return 1;
    }
    op->vkdev = device;
    opt.blob_vkallocator = device->acquire_blob_allocator();
    opt.workspace_vkallocator = opt.blob_vkallocator;
    opt.staging_vkallocator = device->acquire_staging_allocator();
    ncnn::ModelBinFromMatArray weights(0);
    int ret = op->load_param(pd);
    if (!ret) ret = op->load_model(weights);
    if (!ret) ret = op->create_pipeline(opt);
    const int pack = channels % 4 == 0 ? 4 : 1;
    if (!ret && !selected_pipeline(op, name, subgroup, pack)) ret = 1;
    ncnn::Mat output;
    uint64_t dispatches = 0;
    if (!ret)
    {
        ncnn::VkCompute cmd(device);
        ncnn::VkMat gpu;
        // The >65535-channel InstanceNorm case must bypass the separate upload
        // packing shader's dispatch limit. Clone retains its FP32 pack1 layout.
        if (direct_clone) cmd.record_clone(input, gpu, opt);
        else cmd.record_upload(input, gpu, opt);
        // Verify packing and storage actually used, including native FP16 when
        // supported. The upstream general helper disables FP16 arithmetic.
        if (gpu.empty() || gpu.elempack != pack || gpu.elembits() != (opt.use_fp16_storage ? 16 : 32))
            ret = 1;
        const uint64_t before = cmd.pending_dispatch_total();
        if (!ret) ret = op->forward_inplace(gpu, cmd, opt);
        dispatches = cmd.pending_dispatch_total() - before;
        if (!ret && !dispatches) ret = 1;
        if (!ret && spatial)
        {
            const uint64_t expected_dispatches = spatial_dispatches(op, name, gpu, groups, subgroup);
            if (dispatches != expected_dispatches)
            {
                fprintf(stderr, "FAIL: %s workgroups expected=%llu actual=%llu\n", name,
                        static_cast<unsigned long long>(expected_dispatches), static_cast<unsigned long long>(dispatches));
                ret = 1;
            }
        }
        if (!ret)
        {
            if (direct_clone) cmd.record_clone(gpu, output, opt);
            else cmd.record_download(gpu, output, opt);
            ret = cmd.submit_and_wait();
        }
    }
    op->destroy_pipeline(opt);
    delete op;
    device->reclaim_blob_allocator(opt.blob_vkallocator);
    device->reclaim_staging_allocator(opt.staging_vkallocator);
    if (!ret) ret = CompareMat(expected, output, unary ? 0.f : tolerance);
    fprintf(stderr, "%s %s pattern=%s dims=%d width=%d rows=%d channels=%d groups=%d pack=%d fp16=%d requested_subgroup=%d selected=%s dispatch_groups=%llu\n",
            ret ? "FAIL" : "PASS", name, pattern, input.dims, input.w, input.h, channels, spatial ? groups : 0, pack, opt.use_fp16_arithmetic,
            opt.use_subgroup_ops, unary ? "unary" : subgroup ? "subgroup" : "fallback",
            static_cast<unsigned long long>(dispatches));
    return ret;
}

static ncnn::Mat spatial_input(int w, int h, int channels, int pattern, bool half)
{
    ncnn::Mat a(w, h, channels);
    for (int q = 0; q < channels; q++)
    {
        float* p = a.channel(q);
        for (int i = 0; i < w * h; i++)
        {
            if (pattern == 0)
                // FP32 stresses cancellation around a large common offset.
                // FP16 values are exact and leave room for half-stored coeffs.
                p[i] = half ? 64.f + q * 2.f + (i % 7 - 3) * 8.f
                            : 1024.f + q * 0.5f + (i % 7 - 3) * 0.125f;
            else if (pattern == 1)
                // Non-binary FP32 constant stresses long serial sums. The FP16
                // constant and eps give exact coefficients (32, -256).
                p[i] = half ? 8.f : 100.1f;
            else
                p[i] = ((i / 4) % 2 ? -1.f : 1.f) / 256;
        }
        if (pattern == 2)
        {
            // Initial large squares must not erase many later tiny squares.
            for (int i = 0; i < 4; i++) p[i] = 16.f;
            for (int i = 4; i < 8; i++) p[i] = -16.f;
        }
    }
    return a;
}

static int run_spatial_cases(ncnn::VulkanDevice* device, ncnn::Option opt,
                             bool subgroup, bool fp16, int subgroup_size, int& cases)
{
    const int widths[] = {subgroup_size - 1, subgroup_size, subgroup_size + 1, 4097, 257};
    const char* names[] = {"GroupNorm", "InstanceNorm"};
    for (int half = 0; half <= static_cast<int>(fp16); half++)
    {
        opt.use_fp16_packed = opt.use_fp16_storage = opt.use_fp16_arithmetic = half != 0;
        for (int requested = 0; requested < 2; requested++)
        {
            opt.use_subgroup_ops = requested != 0;
            for (int c = 1; c <= 4; c += 3)
            {
                for (int sample = 0; sample < 7; sample++)
                {
                    const int pattern = sample < 5 ? 0 : sample - 4;
                    const int w = sample < 5 ? widths[sample] : sample == 5 ? 256 : 1024;
                    const int h = sample < 4 ? 2 : sample == 4 ? 255 : sample == 5 ? 256 : 512;
                    const ncnn::Mat input = spatial_input(w, h, c, pattern, half != 0);
                    const char* label = pattern == 0 ? "high-offset" : pattern == 1 ? "constant" : "outlier-variance";
                    // Tight FP32 outlier tolerance detects lost small squares;
                    // FP16 includes the existing half coefficient/output error.
                    const float tolerance = half ? (pattern == 0 ? 0.01f : 0.002f)
                                                 : pattern == 2 ? 0.0001f : 0.005f;
                    for (int layer = 0; layer < 2; layer++)
                    {
                        if (run_case(device, names[layer], input, opt, requested && subgroup, 1, label, tolerance)) return 1;
                        cases++;
                    }
                }
            }
            // Group boundaries inside/across packed vec4 lanes: 6, 3, 2 and 1
            // channels per group, including a group that straddles two vec4s.
            const int groups[] = {2, 4, 6, 12};
            const ncnn::Mat mixed = spatial_input(17, 19, 12, 0, half != 0);
            for (int i = 0; i < 4; i++)
            {
                if (run_case(device, "GroupNorm", mixed, opt, requested && subgroup, groups[i],
                             "cross-pack-groups", half ? 0.01f : 0.005f)) return 1;
                cases++;
            }
        }
    }

    opt.use_fp16_packed = opt.use_fp16_storage = opt.use_fp16_arithmetic = false;
    for (int requested = 0; requested < 2; requested++)
    {
        opt.use_subgroup_ops = requested != 0;
        // Match the pinned upstream boundary regressions while making device,
        // selected pipelines and complete dispatch submission mandatory here.
        ncnn::Mat many_groups(65536);
        many_groups.fill(1.f);
        if (run_case(device, "GroupNorm", many_groups, opt, requested && subgroup,
                     65536, "large-group-dispatch")) return 1;
        cases++;
        ncnn::Mat many_channels(2, 1, 65537);
        for (int q = 0; q < many_channels.c; q++)
        {
            float* p = many_channels.channel(q);
            p[0] = -1.f;
            p[1] = 1.f;
        }
        if (run_case(device, "InstanceNorm", many_channels, opt, requested && subgroup,
                     1, "large-channel-dispatch", 0.002f, true)) return 1;
        cases++;
    }
    return 0;
}

int main()
{
    if (ncnn::create_gpu_instance() != 0 || ncnn::get_gpu_count() == 0)
    {
        fprintf(stderr, "FAIL: a working Vulkan device is required; CPU-only/skipped tests do not count\n");
        return 1;
    }
    ncnn::VulkanDevice* device = ncnn::get_gpu_device();
    if (!device) return 1;
    const ncnn::GpuInfo& info = device->info;
    const unsigned required = VK_SUBGROUP_FEATURE_BASIC_BIT | VK_SUBGROUP_FEATURE_ARITHMETIC_BIT;
    const bool subgroup = (info.support_subgroup_ops() & required) == required
                          && info.subgroup_size() >= 4 && info.subgroup_size() <= 128;
    const bool fp16 = info.support_fp16_storage() && info.support_fp16_arithmetic();
    fprintf(stderr, "Vulkan device=%s driver=%s type=%d subgroup_size=%u subgroup_ops=0x%x subgroup_eligible=%d fp16_storage_arithmetic=%d\n",
            info.device_name(), info.driver_name(), info.type(), info.subgroup_size(),
            info.support_subgroup_ops(), subgroup, fp16);
    if (std::getenv("AGENT_REQUIRE_LAVAPIPE")
        && (info.type() != 3 || (!std::strstr(info.device_name(), "llvmpipe") && !std::strstr(info.device_name(), "lavapipe"))))
    {
        fprintf(stderr, "FAIL: CI must select the Mesa lavapipe software Vulkan device\n");
        return 1;
    }
    if (std::getenv("AGENT_REQUIRE_LAVAPIPE") && (!subgroup || !fp16))
    {
        fprintf(stderr, "FAIL: CI requires subgroup and FP16 storage/arithmetic coverage\n");
        return 1;
    }
    if (!subgroup) fprintf(stderr, "UNSUPPORTED: subgroup path; requested-on will verify capability fallback\n");
    if (!fp16) fprintf(stderr, "UNSUPPORTED: FP16 storage/arithmetic; FP32 remains mandatory\n");

    ncnn::Option opt;
    opt.num_threads = 1;
    opt.use_vulkan_compute = true;
    opt.use_packing_layout = true;
    opt.use_fp16_uniform = false;
    opt.use_int8_packed = opt.use_int8_storage = opt.use_int8_arithmetic = false;
    opt.use_bf16_packed = opt.use_bf16_storage = false;
    const int size = std::max(4, std::min(128, static_cast<int>(info.subgroup_size())));
    const int widths[] = {size - 1, size, size + 1, 4097};
    const char* norms[] = {"LayerNorm", "RMSNorm"};
    int cases = 0;
    for (int half = 0; half <= static_cast<int>(fp16); half++)
    {
        opt.use_fp16_packed = opt.use_fp16_storage = opt.use_fp16_arithmetic = half != 0;
        for (int c = 1; c <= 4; c += 3)
        {
            ncnn::Mat ties(8, 2, c);
            const float halfway[] = {0.5f, 1.5f, 2.5f, 3.5f, -0.5f, -1.5f, -2.5f, -3.5f};
            for (int q = 0; q < c; q++)
            {
                float* p = ties.channel(q);
                for (int i = 0; i < ties.w * ties.h; i++) p[i] = halfway[i % 8];
            }
            if (run_case(device, "UnaryOp", ties, opt, false)) return 1;
            cases++;
            for (int requested = 0; requested < 2; requested++)
            {
                opt.use_subgroup_ops = requested != 0;
                for (int w = 0; w < 4; w++)
                {
                    ncnn::Mat input(widths[w], 2, c);
                    for (int q = 0; q < c; q++)
                    {
                        float* p = input.channel(q);
                        for (int i = 0; i < input.w * input.h; i++)
                            p[i] = 8.f + (i % 7 - 3) * 8.f; // exact FP16; square sum exceeds FP16 range
                    }
                    for (int layer = 0; layer < 2; layer++)
                    {
                        if (run_case(device, norms[layer], input, opt, requested && subgroup)) return 1;
                        cases++;
                    }
                }
            }
        }
    }
    if (run_spatial_cases(device, opt, subgroup, fp16, size, cases)) return 1;
    fprintf(stderr, "PASS: %d direct Vulkan operator cases submitted and compared; physical GPU/RISC-V not established\n", cases);
    return 0;
}
