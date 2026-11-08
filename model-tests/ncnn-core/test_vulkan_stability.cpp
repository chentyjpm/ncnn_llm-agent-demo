// Strict direct Vulkan Reduction/LogSumExp/Mish regression: no Net CPU fallback or skips.
// Exit 1 is infrastructure failure; exit 2 + NUMERICAL_MISMATCH requires a
// successfully submitted GPU result which disagrees with a finite reference.
#include "stability_reference.h"
#include "reduction_fp32_accum_reference.h"
#include "allocator.h"
#include "command.h"
#include "gpu.h"
#include "modelbin.h"
#include "pipeline.h"
#include "reduction_vulkan.h"
#include "mish_vulkan.h"
#include "convolutiondepthwise_vulkan.h"

#include <cstdlib>
#include <cstring>

#if !NCNN_VULKAN
#error This regression requires NCNN_VULKAN
#endif

using stability::failure;

struct Resources
{
    ncnn::VkBlobAllocator blob;
    ncnn::VkStagingAllocator staging;
    ncnn::VkWeightAllocator weight;
    ncnn::Option opt;
    Resources(ncnn::VulkanDevice* device, bool half, bool packing, bool subgroup, bool half_arithmetic = true)
        : blob(device), staging(device), weight(device)
    {
        opt.num_threads = 1;
        opt.use_vulkan_compute = true;
        opt.use_packing_layout = packing;
        opt.use_fp16_uniform = false;
        opt.use_fp16_packed = false;
        opt.use_fp16_storage = half;
        opt.use_fp16_arithmetic = half && half_arithmetic;
        opt.use_bf16_packed = opt.use_bf16_storage = false;
        opt.use_int8_packed = opt.use_int8_storage = opt.use_int8_arithmetic = false;
        opt.use_subgroup_ops = subgroup;
        opt.use_cooperative_matrix = false;
        opt.blob_vkallocator = opt.workspace_vkallocator = &blob;
        opt.staging_vkallocator = &staging;
    }
};

struct DestroyPipeline
{
    ncnn::Layer& op;
    const ncnn::Option& opt;
    DestroyPipeline(ncnn::Layer& layer, const ncnn::Option& option) : op(layer), opt(option) {}
    ~DestroyPipeline() { op.destroy_pipeline(opt); }
};

static bool valid(const ncnn::Pipeline* p)
{
    return p && p->pipeline() != VK_NULL_HANDLE && p->local_size_x() && p->local_size_y() && p->local_size_z();
}

static uint64_t groups(const ncnn::Pipeline* p, int w, int h, int c)
{
    return uint64_t((w + p->local_size_x() - 1) / p->local_size_x())
        * ((h + p->local_size_y() - 1) / p->local_size_y())
        * ((c + p->local_size_z() - 1) / p->local_size_z());
}

static bool layout(const ncnn::VkMat& m, int pack, bool half)
{
    return !m.empty() && m.elempack == pack && m.elembits() == (half ? 16 : 32);
}

static int readback(const char* name, ncnn::VkCompute& cmd, const ncnn::VkMat& result,
                    const ncnn::Option& opt, const stability::Reference& expected, bool half)
{
    ncnn::Mat stored;
    const uint64_t before = cmd.pending_dispatch_total();
    // Clone raw storage; perform both casts and unpacking on the CPU so upload
    // and download helpers cannot masquerade as the operator's dispatch.
    cmd.record_clone(result, stored, opt);
    if (cmd.pending_dispatch_total() != before) return failure("unexpected readback dispatch");
    if (cmd.submit_and_wait()) return failure("submit operator and readback");
    if (stored.empty() || stored.elembits() != (half ? 16 : 32) || stored.elempack != result.elempack
        || stored.dims != result.dims || stored.w != result.w || stored.h != result.h
        || stored.d != result.d || stored.c != result.c) return failure("raw readback layout");
    ncnn::Option cpu_opt = opt;
    cpu_opt.use_vulkan_compute = false;
    ncnn::Mat fp32 = stored;
    if (half) ncnn::cast_float16_to_float32(stored, fp32, cpu_opt);
    if (fp32.empty() || fp32.elembits() != 32) return failure("CPU readback cast");
    ncnn::Mat actual = fp32;
    if (fp32.elempack != 1) ncnn::convert_packing(fp32, actual, 1, cpu_opt);
    return stability::compare(name, actual, expected, half);
}

static bool reduction_uses_subgroup(const ncnn::GpuInfo& info)
{
    const uint32_t required = VK_SUBGROUP_FEATURE_BASIC_BIT | VK_SUBGROUP_FEATURE_ARITHMETIC_BIT;
    return (info.support_subgroup_ops() & required) == required;
}

static const char* reduction_path(const ncnn::GpuInfo& info, bool half_arithmetic, bool legacy_shader)
{
    // 09981/36036 gate their subgroup branch on extended FP16 types. 437f18
    // does not: its subgroup accumulator is float even with FP16 arithmetic.
    const bool subgroup = reduction_uses_subgroup(info) && (!legacy_shader || !half_arithmetic
        || info.queryShaderSubgroupExtendedTypesFeatures().shaderSubgroupExtendedTypes);
    return subgroup ? "subgroup" : "shared";
}

static int reduction(ncnn::VulkanDevice* device, const stability::ReductionCase& tc,
                     int pattern, bool half, bool legacy_shader)
{
    std::fprintf(stderr, "RUN Reduction op=10 case=%s pattern=%d fp16=%d path=%s coeff=%.9g keepdims=%d\n",
                 tc.name, pattern, half, reduction_path(device->info, half, legacy_shader), tc.coeff, tc.keepdims);
    const ncnn::Mat input = stability::reduction_input(tc, pattern);
    if (input.empty()) return failure("Reduction input allocation");
    const stability::Reference expected = stability::reduction_reference(tc, input);
    if (expected.layout.empty()) return failure("Reduction reference allocation");
    Resources r(device, half, false, true);
    ncnn::Reduction_vulkan op;
    op.vkdev = device;
    DestroyPipeline cleanup(op, r.opt);
    if (op.load_param(stability::reduction_params(tc)) || op.operation != 10 || !op.support_vulkan
        || op.create_pipeline(r.opt) || !valid(op.pipeline_reduction)) return failure("Reduction op10 pipeline");
    if (op.pipeline_reduction->local_size_x() != 256 || op.pipeline_reduction->local_size_y() != 1
        || op.pipeline_reduction->local_size_z() != 1) return failure("Reduction local size");
    ncnn::VkCompute cmd(device);
    ncnn::VkMat gpu, output;
    cmd.record_upload(input, gpu, r.opt);
    if (!layout(gpu, 1, half) || gpu.dims != input.dims || gpu.w != input.w || gpu.h != input.h
        || gpu.d != input.d || gpu.c != input.c) return failure("Reduction upload shape/storage");
    const uint64_t before = cmd.pending_dispatch_total();
    if (op.forward(gpu, output, cmd, r.opt)) return failure("Reduction forward");
    const uint64_t dispatches = cmd.pending_dispatch_total() - before;
    const uint64_t required = groups(op.pipeline_reduction, 256, stability::count(expected.layout), 1);
    if (!dispatches || dispatches != required || !layout(output, 1, half)) return failure("Reduction dispatch/layout");
    std::fprintf(stderr, "EVIDENCE Reduction pipeline=op10 dispatch_groups=%llu expected_groups=%llu\n",
                 (unsigned long long)dispatches, (unsigned long long)required);
    return readback("Reduction", cmd, output, r.opt, expected, half);
}

static int reduction_fp32_accum(ncnn::VulkanDevice* device, const stability::ReductionCase& tc,
                                int operation, int pattern, int storage_mode, bool legacy_shader)
{
    const bool half = storage_mode != 0;
    const bool half_arithmetic = storage_mode == 2;
    const char* name = accumulation::operation_name(operation);
    std::fprintf(stderr, "RUN ReductionFP32Accum op=%d name=%s case=%s pattern=%d fp16_storage=%d fp16_arithmetic=%d path=%s coeff=%.9g keepdims=%d\n",
                 operation, name, tc.name, pattern, half, half_arithmetic, reduction_path(device->info, half_arithmetic, legacy_shader),
                 accumulation::coefficient(tc, operation), tc.keepdims);
    const ncnn::Mat input = accumulation::input(tc, operation, pattern);
    if (input.empty()) return failure("FP32 accumulation input allocation");
    const accumulation::Reference expected = accumulation::reference(tc, input, operation);
    if (!accumulation::valid_fixture(input, expected)) return failure("FP32 accumulation finite adversarial oracle");
    Resources r(device, half, false, true, half_arithmetic);
    ncnn::Option cpu_opt = r.opt;
    cpu_opt.use_vulkan_compute = false;
    ncnn::Mat storage;
    if (!accumulation::exact_storage(input, half, storage, cpu_opt)) return failure("FP32 accumulation input storage conversion must be exact and finite");
    ncnn::Reduction_vulkan op;
    op.vkdev = device;
    DestroyPipeline cleanup(op, r.opt);
    ncnn::ParamDict pd = stability::reduction_params(tc);
    pd.set(0, operation);
    pd.set(2, accumulation::coefficient(tc, operation));
    if (op.load_param(pd) || op.operation != operation || !op.support_vulkan
        || op.create_pipeline(r.opt) || !valid(op.pipeline_reduction)) return failure("FP32 accumulation Reduction pipeline");
    if (op.pipeline_reduction->local_size_x() != 256 || op.pipeline_reduction->local_size_y() != 1
        || op.pipeline_reduction->local_size_z() != 1) return failure("FP32 accumulation Reduction local size");
    ncnn::VkCompute cmd(device);
    ncnn::VkMat gpu, output;
    // Raw exact storage avoids any FP16 conversion dispatch in the GPU upload.
    // Only the real Reduction_vulkan::forward contributes compute work.
    cmd.record_clone(storage, gpu, r.opt);
    if (cmd.pending_dispatch_total() || !layout(gpu, 1, half) || gpu.dims != input.dims
        || gpu.w != input.w || gpu.h != input.h || gpu.d != input.d || gpu.c != input.c)
        return failure("FP32 accumulation raw input upload shape/storage");
    if (op.forward(gpu, output, cmd, r.opt)) return failure("FP32 accumulation Reduction forward");
    const uint64_t dispatches = cmd.pending_dispatch_total();
    const uint64_t required = groups(op.pipeline_reduction, 256, stability::count(expected.expected.layout), 1);
    if (!dispatches || dispatches != required || !layout(output, 1, half)) return failure("FP32 accumulation dispatch/layout");
    std::fprintf(stderr, "EVIDENCE ReductionFP32Accum pipeline=op%d path=%s exact_finite_input=1 min_accumulator=%.17g max_accumulator=%.17g dispatch_groups=%llu expected_groups=%llu\n",
                 operation, reduction_path(device->info, half_arithmetic, legacy_shader),
                 *std::min_element(expected.accumulators.begin(), expected.accumulators.end()),
                 *std::max_element(expected.accumulators.begin(), expected.accumulators.end()),
                 (unsigned long long)dispatches, (unsigned long long)required);
    return readback(name, cmd, output, r.opt, expected.expected, half);
}

static ncnn::Mat mish_input(int channels)
{
    static const float values[] = {-1000.f, -100.f, -20.f, -12.f, -10.f, -6.f, -2.f, -1.f, -0.25f,
        0.f, 0.25f, 1.f, 2.f, 6.f, 9.5f, 10.f, 10.5f, 11.f, 11.09375f, 11.125f,
        12.f, 20.f, 80.f, 88.f, 89.f, 100.f, 1000.f, 60000.f, 65504.f};
    ncnn::Mat input(29, 2, channels);
    if (input.empty()) return input;
    input.fill(0.f);
    for (int i = 0; i < stability::count(input); i++) stability::value(input, i) = values[i % 29];
    return input;
}

static stability::Reference mish_reference(const ncnn::Mat& input)
{
    stability::Reference result;
    result.layout = input;
    for (int i = 0; i < stability::count(input); i++)
    {
        const double x = stability::value(input, i);
        // Stable softplus in double, not the shader's clamp or naive FP32 exp.
        const double softplus = std::max(x, 0.) + std::log1p(std::exp(-std::fabs(x)));
        result.values.push_back(x * std::tanh(softplus));
    }
    return result;
}

static int mish(ncnn::VulkanDevice* device, bool half, int channels, bool fused)
{
    const int pack = channels == 4 ? 4 : 1;
    const char* name = fused ? "FusedMish" : "Mish";
    std::fprintf(stderr, "RUN %s fp16=%d channels=%d pack=%d fused_helper=%s\n",
                 name, half, channels, pack, fused ? (pack == 4 ? "activation_afpvec4" : "activation_afp") : "standalone_vec4");
    const ncnn::Mat input = mish_input(channels);
    if (input.empty()) return failure("Mish input allocation");
    const stability::Reference expected = mish_reference(input);
    Resources r(device, half, true, false);
    ncnn::Mish_vulkan standalone;
    ncnn::ConvolutionDepthWise_vulkan depthwise;
    standalone.vkdev = depthwise.vkdev = device;
    ncnn::Layer& op = fused ? static_cast<ncnn::Layer&>(depthwise) : static_cast<ncnn::Layer&>(standalone);
    DestroyPipeline cleanup(op, r.opt);
    ncnn::ParamDict pd;
    if (fused)
    {
        // Exactly one multiply by 1 and no bias: the convolution is an identity
        // and the only nontrivial math is shared fused Mish activation type 5.
        pd.set(0, channels);
        pd.set(1, 1);
        pd.set(5, 0);
        pd.set(6, channels);
        pd.set(7, channels);
        pd.set(9, 5);
    }
    if (op.load_param(pd) || !op.support_vulkan) return failure("Mish parameters");
    if (fused)
    {
        ncnn::Mat weights(channels);
        if (weights.empty()) return failure("Mish identity weight allocation");
        weights.fill(1.f);
        ncnn::ModelBinFromMatArray mb(&weights);
        if (op.load_model(mb)) return failure("Mish identity model");
    }
    if (op.create_pipeline(r.opt)) return failure("Mish create pipeline");
    const ncnn::Pipeline* pipeline = fused ? (pack == 4 ? depthwise.pipeline_convolutiondepthwise_pack4
        : depthwise.pipeline_convolutiondepthwise) : standalone.pipeline_mish;
    if (!valid(pipeline)) return failure("Mish selected scalar/vector pipeline");
    uint64_t upload_bytes = 0;
    if (fused)
    {
        if ((pack == 4 ? depthwise.pipeline_convolutiondepthwise : depthwise.pipeline_convolutiondepthwise_pack4)
            || depthwise.pipeline_convolutiondepthwise_group || depthwise.pipeline_convolutiondepthwise_group_pack4)
            return failure("unexpected fused Mish alternative pipeline");
        ncnn::Option weight_opt = r.opt;
        weight_opt.blob_vkallocator = &r.weight;
        ncnn::VkTransfer transfer(device);
        if (op.upload_model(transfer, weight_opt)) return failure("record Mish weight upload");
        upload_bytes = transfer.pending_upload_total();
        if (!upload_bytes || transfer.submit_and_wait() || !layout(depthwise.weight_data_gpu, pack, half))
            return failure("submit Mish weight upload/storage");
    }
    ncnn::VkCompute cmd(device);
    ncnn::VkMat gpu, output;
    cmd.record_upload(input, gpu, r.opt);
    if (!layout(gpu, pack, half) || gpu.dims != 3 || gpu.w != input.w || gpu.h != input.h
        || gpu.c * pack != channels) return failure("Mish upload packing/storage");
    const uint64_t before = cmd.pending_dispatch_total();
    if (fused)
    {
        if (depthwise.forward(gpu, output, cmd, r.opt)) return failure("fused Mish forward");
    }
    else
    {
        output = gpu;
        if (standalone.forward_inplace(output, cmd, r.opt)) return failure("standalone Mish forward");
    }
    const uint64_t dispatches = cmd.pending_dispatch_total() - before;
    const uint64_t required = fused ? groups(pipeline, gpu.w, gpu.h, gpu.c)
        : groups(pipeline, int(gpu.total() * gpu.elempack / 4), 1, 1);
    if (!dispatches || dispatches != required || !layout(output, pack, half)) return failure("Mish dispatch/layout");
    std::fprintf(stderr, "EVIDENCE %s pipeline=valid pack=%d upload_bytes=%llu dispatch_groups=%llu expected_groups=%llu\n",
                 name, pack, (unsigned long long)upload_bytes, (unsigned long long)dispatches, (unsigned long long)required);
    return readback(name, cmd, output, r.opt, expected, half);
}

static int run(ncnn::VulkanDevice* device, bool run_reduction, bool run_mish, bool run_accum, const char* required_path, bool legacy_shader)
{
    if (!device) return failure("missing Vulkan device");
    const ncnn::GpuInfo& info = device->info;
    std::fprintf(stderr, "Vulkan device=%s driver=%s fp16_storage=%d fp16_arithmetic=%d subgroup_ops=0x%x\n",
                 info.device_name(), info.driver_name(), info.support_fp16_storage(), info.support_fp16_arithmetic(), info.support_subgroup_ops());
    if (!info.support_fp16_storage() || !info.support_fp16_arithmetic()) return failure("FP16 storage/arithmetic required; no skip");
    // reduction.comp selects subgroup code from device feature macros, not
    // opt.use_subgroup_ops. Toggling that option on the same device would NOT
    // cover the shared-memory fallback; do not claim two paths from that toggle.
    if (run_reduction || run_accum)
    {
        const char* fp32_path = reduction_path(info, false, legacy_shader);
        const char* fp16_path = reduction_path(info, true, legacy_shader);
        std::fprintf(stderr, "CAPABILITY Reduction shader_contract=%s fp32_path=%s fp16_arithmetic_path=%s subgroup_size=%u extended_types=%d (not required by 437f18); %s\n",
                     legacy_shader ? "legacy_fp16" : "437f18_float", fp32_path, fp16_path, info.subgroup_size(),
                     info.queryShaderSubgroupExtendedTypesFeatures().shaderSubgroupExtendedTypes,
                     std::strcmp(fp32_path, fp16_path) == 0 ? "other_path=NOT_COVERED" : "paths=precision_dependent");
        if (required_path && std::strcmp(required_path, fp32_path) != 0)
            return failure("requested Reduction shader path unavailable on this device; no skipped success");
    }
    if (std::getenv("AGENT_REQUIRE_LAVAPIPE") && (info.type() != 3
        || (!std::strstr(info.device_name(), "llvmpipe") && !std::strstr(info.device_name(), "lavapipe"))))
        return failure("CI must select Mesa lavapipe software Vulkan");
    int cases = 0, failures = 0;
    for (int half = 0; half < 2; half++)
    {
        if (run_reduction)
            for (size_t i = 0; i < sizeof(stability::reduction_cases) / sizeof(stability::reduction_cases[0]); i++)
                for (int pattern = 0; pattern < 3; pattern++)
                {
                    const int ret = reduction(device, stability::reduction_cases[i], pattern, half != 0, legacy_shader);
                    if (ret == 1) return 1;
                    failures += ret == 2;
                    cases++;
                }
        if (run_mish)
            for (int channels = 1; channels <= 4; channels += 3)
                for (int fused = 0; fused < 2; fused++)
                {
                    const int ret = mish(device, half != 0, channels, fused != 0);
                    if (ret == 1) return 1;
                    failures += ret == 2;
                    cases++;
                }
    }
    if (run_accum)
        for (int mode = 0; mode < 3; mode++)
            for (size_t op = 0; op < sizeof(accumulation::operations) / sizeof(accumulation::operations[0]); op++)
                for (size_t i = 0; i < sizeof(accumulation::cases) / sizeof(accumulation::cases[0]); i++)
                    for (int pattern = 0; pattern < 2; pattern++)
                    {
                        const int ret = reduction_fp32_accum(device, accumulation::cases[i], accumulation::operations[op], pattern, mode, legacy_shader);
                        if (ret == 1) return 1;
                        failures += ret == 2;
                        cases++;
                    }
    std::fprintf(stderr, "%s: %d submitted Vulkan stability cases; numerical_failures=%d; software Vulkan is not physical GPU/model validation\n",
                 failures ? "FAIL" : "PASS", cases, failures);
    return failures ? 2 : 0;
}

int main(int argc, char** argv)
{
    bool reduction = true, mish = true, accum = true;
    bool selected_suite = false, legacy_shader = false;
    const char* required_path = 0;
    for (int i = 1; i < argc; i++)
    {
        if (!selected_suite && std::strcmp(argv[i], "--reduction") == 0)
        { reduction = true; mish = accum = false; selected_suite = true; }
        else if (!selected_suite && std::strcmp(argv[i], "--mish") == 0)
        { mish = true; reduction = accum = false; selected_suite = true; }
        else if (!selected_suite && std::strcmp(argv[i], "--reduction-fp32-accum") == 0)
        { accum = true; reduction = mish = false; selected_suite = true; }
        // Reporting/requirement contract ONLY. The workflow must separately
        // replace and rebuild the old .comp; this flag never changes a shader.
        else if (!legacy_shader && std::strcmp(argv[i], "--legacy-fp16-reduction-shader") == 0) legacy_shader = true;
        else if (!required_path && std::strcmp(argv[i], "--require-reduction-path=subgroup") == 0) required_path = "subgroup";
        else if (!required_path && std::strcmp(argv[i], "--require-reduction-path=shared") == 0) required_path = "shared";
        else return failure("usage: test_vulkan_stability [--reduction|--mish|--reduction-fp32-accum] [--require-reduction-path=subgroup|shared | --legacy-fp16-reduction-shader]");
    }
    if ((required_path || legacy_shader) && !reduction && !accum) return failure("Reduction path contract is invalid for Mish-only suite");
    if (required_path && legacy_shader) return failure("path requirement applies to current shaders; legacy precision paths are reported separately");
    if (ncnn::create_gpu_instance() != 0) return failure("a working Vulkan device is required; cannot create Vulkan instance");
    const int ret = ncnn::get_gpu_count() ? run(ncnn::get_gpu_device(), reduction, mish, accum, required_path, legacy_shader)
        : failure("a working Vulkan device is required; no CPU fallback or skipped success");
    ncnn::destroy_gpu_instance();
    return ret;
}
