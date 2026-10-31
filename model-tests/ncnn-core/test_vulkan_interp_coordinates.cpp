// Strict Interp regression for ncnn #7198. No Net CPU fallback or GPU skips.
// Exit 2 means an actual submitted GPU result disagreed numerically; exit 1 is
// an infrastructure/shape/pipeline failure (never a successful negative control).
#include "testutil.h"
#include "command.h"
#include "gpu.h"
#include "pipeline.h"
#include "interp_vulkan.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>

#if !NCNN_VULKAN
#error This regression requires NCNN_VULKAN
#endif

static bool valid(const ncnn::Pipeline* p)
{
    return p && p->pipeline() != VK_NULL_HANDLE;
}

static uint64_t groups(const ncnn::Pipeline* p, int w, int h, int c)
{
    return uint64_t((w + p->local_size_x() - 1) / p->local_size_x())
           * ((h + p->local_size_y() - 1) / p->local_size_y())
           * ((c + p->local_size_z() - 1) / p->local_size_z());
}

static int run_case(ncnn::VulkanDevice* device, int type, int dims, int w, int h,
                    int channels, int outw, int outh, int align, bool half)
{
    ncnn::Mat input = dims == 2 ? ncnn::Mat(w, channels) : ncnn::Mat(w, h, channels);
    // All input values are exactly representable in FP16. A bounded, non-linear
    // pattern exposes wrong source indices without hiding them behind the
    // large relative tolerance of a coordinate ramp.
    for (int q = 0; q < channels; q++)
    {
        float* p = dims == 2 ? input.row(q) : input.channel(q);
        for (int y = 0; y < (dims == 2 ? 1 : h); y++)
            for (int x = 0; x < w; x++)
                p[y * w + x] = float(((x * 13 + y * 7 + q * 3) % 17) - 8) * 0.5f;
    }
    ncnn::ParamDict pd;
    pd.set(0, type);
    pd.set(1, 1.f);
    pd.set(2, 1.f);
    pd.set(3, dims == 2 ? channels : outh);
    pd.set(4, outw);
    pd.set(6, align);
    ncnn::Mat expected;
    // Explicit naive CPU reference: does not run the optimized Vulkan shader.
    if (test_layer_naive(ncnn::layer_to_index("Interp"), pd, std::vector<ncnn::Mat>(), input, expected, 0))
        return 1;

    ncnn::Option opt;
    opt.num_threads = 1;
    opt.use_vulkan_compute = true;
    opt.use_packing_layout = true;
    opt.use_fp16_uniform = false;
    opt.use_fp16_packed = opt.use_fp16_storage = opt.use_fp16_arithmetic = half;
    opt.use_bf16_packed = opt.use_bf16_storage = false;
    opt.use_int8_packed = opt.use_int8_storage = opt.use_int8_arithmetic = false;
    opt.blob_vkallocator = device->acquire_blob_allocator();
    opt.workspace_vkallocator = opt.blob_vkallocator;
    opt.staging_vkallocator = device->acquire_staging_allocator();

    ncnn::Interp_vulkan op;
    op.vkdev = device;
    int ret = op.load_param(pd);
    if (!ret) ret = op.create_pipeline(opt);
    const int pack = channels == 4 ? 4 : 1;
    const ncnn::Pipeline* pipeline = type == 3
        ? (pack == 4 ? op.pipeline_interp_bicubic_pack4 : op.pipeline_interp_bicubic)
        : (pack == 4 ? op.pipeline_interp_pack4 : op.pipeline_interp);
    if (!ret && (!valid(pipeline) || (type == 3 && (!valid(op.pipeline_interp_bicubic_coeffs_x)
        || (dims == 3 && !valid(op.pipeline_interp_bicubic_coeffs_y)))))) ret = 1;

    ncnn::Mat output;
    uint64_t dispatches = 0;
    if (!ret)
    {
        ncnn::VkCompute cmd(device);
        ncnn::VkMat gpu, result;
        cmd.record_upload(input, gpu, opt);
        if (gpu.empty() || gpu.elempack != pack || gpu.elembits() != (half ? 16 : 32)) ret = 1;
        const uint64_t before = cmd.pending_dispatch_total();
        if (!ret) ret = op.forward(gpu, result, cmd, opt);
        dispatches = cmd.pending_dispatch_total() - before;
        if (!ret && (result.empty() || result.elempack != pack || result.elembits() != (half ? 16 : 32))) ret = 1;
        if (!ret)
        {
            uint64_t required = groups(pipeline, result.w, result.h, result.c);
            if (type == 3)
            {
                required += groups(op.pipeline_interp_bicubic_coeffs_x, outw, 1, 1);
                if (dims == 3) required += groups(op.pipeline_interp_bicubic_coeffs_y, outh, 1, 1);
            }
            if (!dispatches || dispatches != required)
            {
                fprintf(stderr, "FAIL: Interp dispatch groups actual=%llu expected=%llu\n",
                        (unsigned long long)dispatches, (unsigned long long)required);
                ret = 1;
            }
        }
        if (!ret)
        {
            // Keep the actual shader packed, but normalize the readback to the
            // unpacked naive CPU reference rather than rejecting valid pack4.
            ncnn::Option download_opt = opt;
            download_opt.use_packing_layout = false;
            cmd.record_download(result, output, download_opt);
            ret = cmd.submit_and_wait();
        }
    }
    op.destroy_pipeline(opt);
    device->reclaim_blob_allocator(opt.blob_vkallocator);
    device->reclaim_staging_allocator(opt.staging_vkallocator);
    if (ret) return 1;
    if (output.empty() || output.dims != expected.dims || output.w != expected.w
        || output.h != expected.h || output.c != expected.c || output.elempack != 1 || output.elembits() != 32)
    {
        fprintf(stderr, "FAIL: Interp download/reference shape or layout mismatch\n");
        return 1;
    }
    // Absolute (not relative) error: wrong indices change this pattern by order
    // one. FP16 interpolation rounds stored weights, arithmetic and output.
    // CPU reference scale is double while the shader receives float; at large
    // aligned coordinates their bicubic result can differ by ~0.00055 even
    // without FP16. This bound remains far below old wrong-index errors, which
    // are independently required to fail the old-shader negative control.
    const float tolerance = type == 1 ? 0.f : half ? 0.02f : 0.002f;
    float maximum = 0.f;
    int mismatches = 0;
    for (int q = 0; q < expected.c; q++)
    {
        const float* e = expected.channel(q);
        const float* a = output.channel(q);
        for (int i = 0; i < expected.w * expected.h; i++)
        {
            const float error = std::fabs(a[i] - e[i]);
            maximum = std::max(maximum, error);
            if (!std::isfinite(a[i]) || error > tolerance)
            {
                if (!mismatches) fprintf(stderr, "NUMERICAL_MISMATCH q=%d index=%d expected=%.9g actual=%.9g tolerance=%.9g\n", q, i, e[i], a[i], tolerance);
                mismatches++;
            }
        }
    }
    fprintf(stderr, "%s Interp mode=%d dims=%d input=%dx%d channels=%d output=%dx%d pack=%d align=%d fp16_storage_arithmetic=%d dispatch_groups=%llu max_abs_error=%.9g mismatches=%d\n",
            mismatches ? "FAIL" : "PASS", type, dims, w, h, channels, outw, outh, pack, align, half,
            (unsigned long long)dispatches, maximum, mismatches);
    return mismatches ? 2 : 0;
}

int main()
{
    if (ncnn::create_gpu_instance() != 0 || ncnn::get_gpu_count() == 0)
    {
        fprintf(stderr, "FAIL: a working Vulkan device is required; no CPU fallback or skips\n");
        return 1;
    }
    ncnn::VulkanDevice* device = ncnn::get_gpu_device();
    if (!device) return 1;
    const ncnn::GpuInfo& info = device->info;
    fprintf(stderr, "Vulkan device=%s driver=%s fp16_storage=%d fp16_arithmetic=%d\n",
            info.device_name(), info.driver_name(), info.support_fp16_storage(), info.support_fp16_arithmetic());
    if (!info.support_fp16_storage() || !info.support_fp16_arithmetic())
    {
        fprintf(stderr, "FAIL: FP16 storage and arithmetic are mandatory for coordinate regression\n");
        return 1;
    }
    if (std::getenv("AGENT_REQUIRE_LAVAPIPE") && (info.type() != 3
        || (!std::strstr(info.device_name(), "llvmpipe") && !std::strstr(info.device_name(), "lavapipe"))))
    {
        fprintf(stderr, "FAIL: CI must select the Mesa lavapipe software Vulkan device\n");
        return 1;
    }
    int cases = 0, numerical_failures = 0;
    for (int half = 0; half < 2; half++)
        for (int channels = 1; channels <= 4; channels += 3)
            for (int type = 1; type <= 3; type++)
                for (int align = 0; align <= (type == 1 ? 0 : 1); align++)
                    for (int shape = 0; shape < (type == 2 ? 5 : 3); shape++)
                    {
                        // >1024 loses half-pixel offsets, >2048 loses odd integer
                        // coordinates. Both axes and dims=2 have distinct branches.
                        const int size = type == 2 ? 640 : 700;
                        const int out = type == 2 ? 1920 : 2100;
                        const int dims = shape == 0 ? 2 : 3;
                        const bool vertical = shape == 2 || shape == 4;
                        const int narrow = shape >= 3 ? 1 : 5;
                        const int w = vertical ? narrow : size;
                        const int h = vertical ? size : narrow;
                        const int outw = vertical ? 7 : out;
                        const int outh = vertical ? out : 7;
                        const int ret = run_case(device, type, dims, w, h, channels, outw, outh, align, half != 0);
                        if (ret == 1)
                        {
                            fprintf(stderr, "INFRASTRUCTURE_FAILURE mode=%d dims=%d input=%dx%d channels=%d output=%dx%d align=%d fp16=%d\n",
                                    type, dims, w, h, channels, outw, outh, align, half);
                            return 1;
                        }
                        numerical_failures += ret == 2;
                        cases++;
                    }
    fprintf(stderr, "%s: %d submitted Interp cases; numerical_failures=%d; software Vulkan is not physical GPU/model validation\n",
            numerical_failures ? "FAIL" : "PASS", cases, numerical_failures);
    return numerical_failures ? 2 : 0;
}
