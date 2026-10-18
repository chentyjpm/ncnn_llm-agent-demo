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

#include <algorithm>
#include <cstdlib>
#include <cstring>

#if !NCNN_VULKAN
#error This regression must be built with NCNN_VULKAN enabled
#endif

static bool valid(const ncnn::Pipeline* p)
{
    return p && p->pipeline() != VK_NULL_HANDLE;
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
    const ncnn::RMSNorm_vulkan* p = static_cast<ncnn::RMSNorm_vulkan*>(op);
    const ncnn::Pipeline* reduce = pack == 4 ? p->pipeline_rmsnorm_reduce_subgroup_pack4 : p->pipeline_rmsnorm_reduce_subgroup;
    const ncnn::Pipeline* fallback = pack == 4 ? p->pipeline_rmsnorm_reduce_sum4_fp16_to_fp32_pack4 : p->pipeline_rmsnorm_reduce_sum4_fp16_to_fp32;
    return subgroup ? valid(reduce) : !reduce && valid(fallback);
}

static int run_case(ncnn::VulkanDevice* device, const char* name, const ncnn::Mat& input,
                    ncnn::Option opt, bool subgroup)
{
    const bool unary = std::strcmp(name, "UnaryOp") == 0;
    ncnn::ParamDict pd;
    pd.set(0, unary ? 18 : input.w);
    if (!unary)
    {
        pd.set(1, 0.001f);
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
    const int pack = input.c % 4 == 0 ? 4 : 1;
    if (!ret && !selected_pipeline(op, name, subgroup, pack)) ret = 1;
    ncnn::Mat output;
    uint64_t dispatches = 0;
    if (!ret)
    {
        ncnn::VkCompute cmd(device);
        ncnn::VkMat gpu;
        cmd.record_upload(input, gpu, opt);
        // Verify packing and storage actually used, including native FP16 when
        // supported. The upstream general helper disables FP16 arithmetic.
        if (gpu.empty() || gpu.elempack != pack || gpu.elembits() != (opt.use_fp16_storage ? 16 : 32))
            ret = 1;
        const uint64_t before = cmd.pending_dispatch_total();
        if (!ret) ret = op->forward_inplace(gpu, cmd, opt);
        dispatches = cmd.pending_dispatch_total() - before;
        if (!ret && !dispatches) ret = 1;
        if (!ret)
        {
            cmd.record_download(gpu, output, opt);
            ret = cmd.submit_and_wait();
        }
    }
    op->destroy_pipeline(opt);
    delete op;
    device->reclaim_blob_allocator(opt.blob_vkallocator);
    device->reclaim_staging_allocator(opt.staging_vkallocator);
    if (!ret) ret = CompareMat(expected, output, unary ? 0.f : 0.002f);
    fprintf(stderr, "%s %s width=%d rows=%d pack=%d fp16=%d requested_subgroup=%d selected=%s dispatch_groups=%llu\n",
            ret ? "FAIL" : "PASS", name, input.w, input.h, pack, opt.use_fp16_arithmetic,
            opt.use_subgroup_ops, unary ? "unary" : subgroup ? "subgroup" : "fallback",
            static_cast<unsigned long long>(dispatches));
    return ret;
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
    if (!subgroup) fprintf(stderr, "UNSUPPORTED: subgroup path; requested-on will verify capability fallback\n");
    if (!fp16) fprintf(stderr, "UNSUPPORTED: FP16 storage/arithmetic; FP32 remains mandatory\n");

    ncnn::Option opt;
    opt.num_threads = 1;
    opt.use_vulkan_compute = true;
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
    fprintf(stderr, "PASS: %d direct Vulkan operator cases submitted and compared; physical GPU/RISC-V not established\n", cases);
    return 0;
}
