// Direct Vulkan regression companion. All observed Vulkan calls are forwarded
// unchanged to the real driver; this is not an emulator or a CPU fallback.
#include "testutil.h"
#include "allocator.h"
#include "command.h"
#include "gpu.h"
#include "pipeline.h"
#include "gemm_vulkan.h"
#include "sdpa_vulkan.h"
#include "softmax_vulkan.h"
#include "memorydata_vulkan.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <map>

#if !NCNN_VULKAN
#error This regression requires Vulkan
#endif

static int failure(const char* function, int line)
{
    fprintf(stderr, "FAIL: %s at companion source line %d (see RUN case above)\n", function, line);
    return 1;
}

static bool valid(const ncnn::Pipeline* p)
{
    return p && p->pipeline() != VK_NULL_HANDLE;
}

static uint64_t workgroups(const ncnn::Pipeline* p, int w, int h, int c)
{
    return uint64_t((w + p->local_size_x() - 1) / p->local_size_x())
           * ((h + p->local_size_y() - 1) / p->local_size_y())
           * ((c + p->local_size_z() - 1) / p->local_size_z());
}

struct Allocators
{
    ncnn::VkBlobAllocator blob;
    ncnn::VkStagingAllocator staging;
    ncnn::Option opt;
    Allocators(ncnn::VulkanDevice* device, int storage) : blob(device), staging(device)
    {
        opt.num_threads = 1;
        opt.use_vulkan_compute = true;
        opt.use_packing_layout = true;
        // Packed FP16 must not accidentally select native FP16 storage.
        opt.use_fp16_packed = storage == 1;
        opt.use_fp16_storage = storage == 2;
        opt.use_fp16_arithmetic = false;
        opt.use_fp16_uniform = false;
        opt.use_bf16_packed = opt.use_bf16_storage = false;
        opt.use_int8_packed = opt.use_int8_storage = opt.use_int8_arithmetic = false;
        opt.use_cooperative_matrix = false;
        opt.use_shader_local_memory = false;
        opt.use_subgroup_ops = false;
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

static const char* storage_name(int storage)
{
    return storage == 0 ? "fp32" : storage == 1 ? "fp16-packed" : "fp16-storage";
}

static void fill(ncnn::Mat& a, int seed)
{
    for (int q = 0; q < a.c; q++)
    {
        float* p = a.channel(q);
        for (int i = 0; i < a.w * a.h; i++)
            p[i] = float((i * 5 + q * 7 + seed * 3) % 23 - 11) / 16;
    }
}

// Explicit pack1 uploads preserve scalar half addressing and keep upload
// packing work out of the operator workgroup assertions.
static bool upload(ncnn::VkCompute& cmd, const ncnn::Mat& input, ncnn::VkMat& gpu,
                   const ncnn::Option& opt, int storage)
{
    ncnn::Mat stored = input;
    if (storage) ncnn::cast_float32_to_float16(input, stored, opt);
    cmd.record_clone(stored, gpu, opt);
    return !gpu.empty() && gpu.elempack == 1 && gpu.elembits() == (storage ? 16 : 32);
}

static int compare_download(const ncnn::Mat& expected, const ncnn::Mat& stored,
                            const ncnn::Option& opt, int storage, float tolerance)
{
    ncnn::Mat fp32 = stored;
    if (storage) ncnn::cast_float16_to_float32(stored, fp32, opt);
    ncnn::Mat unpacked;
    ncnn::convert_packing(fp32, unpacked, 1, opt);
    return CompareMat(expected, unpacked, tolerance);
}

static bool gemm_subgroup_supported(const ncnn::GpuInfo& info)
{
    const unsigned required = VK_SUBGROUP_FEATURE_BASIC_BIT | VK_SUBGROUP_FEATURE_SHUFFLE_BIT;
    return (info.support_subgroup_ops() & required) == required
           && info.subgroup_size() >= 4 && info.subgroup_size() <= 128
           && (info.vendor_id() != 0x5143 || info.queryMaintenance4Features().maintenance4);
}

static int gemm_case(ncnn::VulkanDevice* device, int storage, bool requested_subgroup,
                     int M, int N, int K, bool transpose)
{
    fprintf(stderr, "RUN Gemm storage=%s M=%d N=%d K=%d transAB=%d requested_subgroup=%d\n",
            storage_name(storage), M, N, K, transpose, requested_subgroup);
    Allocators alloc(device, storage);
    ncnn::Option& opt = alloc.opt;
    opt.use_subgroup_ops = requested_subgroup;
    ncnn::Mat a(transpose ? M : K, transpose ? K : M);
    ncnn::Mat b(transpose ? K : N, transpose ? N : K);
    ncnn::Mat c(N, M);
    fill(a, 1);
    fill(b, 2);
    fill(c, 3); // full MxN C (broadcast_type_C=3), never a scalar/row bias
    ncnn::Mat expected(N, M);
    for (int i = 0; i < M; i++)
        for (int j = 0; j < N; j++)
        {
            double sum = 0;
            for (int k = 0; k < K; k++)
                sum += double(transpose ? a.row(k)[i] : a.row(i)[k])
                       * (transpose ? b.row(j)[k] : b.row(k)[j]);
            expected.row(i)[j] = float(sum + c.row(i)[j]);
        }
    ncnn::Gemm_vulkan op;
    op.vkdev = device;
    DestroyPipeline cleanup(op, opt);
    ncnn::ParamDict pd;
    pd.set(2, int(transpose));
    pd.set(3, int(transpose));
    if (op.load_param(pd) || op.load_model(ncnn::ModelBinFromMatArray(0)) || op.create_pipeline(opt)) return failure(__func__, __LINE__);
    const bool subgroup = requested_subgroup && gemm_subgroup_supported(device->info);
    if (!valid(op.pipeline_gemm) || op.use_cooperative_matrix || op.use_subgroup_ops != subgroup) return failure(__func__, __LINE__);
    ncnn::VkCompute cmd(device);
    std::vector<ncnn::VkMat> in(3), out(1);
    if (!upload(cmd, a, in[0], opt, storage) || !upload(cmd, b, in[1], opt, storage)
        || !upload(cmd, c, in[2], opt, storage)) return failure(__func__, __LINE__);
    const uint64_t before = cmd.pending_dispatch_total();
    if (op.forward(in, out, cmd, opt)) return failure(__func__, __LINE__);
    const uint64_t actual = cmd.pending_dispatch_total() - before;
    const uint64_t required = subgroup
        ? uint64_t((M + op.UNROLL_SG_M * 4 - 1) / (op.UNROLL_SG_M * 4))
          * ((N + op.UNROLL_SG_N * 4 - 1) / (op.UNROLL_SG_N * 4))
        : workgroups(op.pipeline_gemm, (N + 3) / 4, (M + 3) / 4, 1);
    const int pack = M % 4 == 0 ? 4 : 1;
    if (actual != required)
        fprintf(stderr, "FAIL: expected %llu operator workgroups, observed %llu\n",
                static_cast<unsigned long long>(required), static_cast<unsigned long long>(actual));
    if (actual != required || !actual || out[0].empty() || out[0].elempack != pack
        || out[0].elembits() != (storage ? 16 : 32)) return failure(__func__, __LINE__);
    ncnn::Mat downloaded;
    cmd.record_clone(out[0], downloaded, opt);
    if (cmd.submit_and_wait()) return failure(__func__, __LINE__);
    const int ret = compare_download(expected, downloaded, opt, storage, storage ? 0.002f : 0.0001f);
    fprintf(stderr, "%s Gemm storage=%s M=%d N=%d K=%d transAB=%d output_pack=%d requested_subgroup=%d selected=%s dispatch_groups=%llu\n",
            ret ? "FAIL" : "PASS", storage_name(storage), M, N, K, transpose, pack, requested_subgroup,
            subgroup ? "gemm_sg" : "gemm", static_cast<unsigned long long>(actual));
    return ret;
}

static ncnn::Mat attention_reference(const ncnn::Mat& q, const ncnn::Mat& k,
                                     const ncnn::Mat& v, const ncnn::Mat& mask)
{
    ncnn::Mat out(v.w, q.h, q.c);
    for (int h = 0; h < q.c; h++)
    {
        const ncnn::Mat query = q.channel(h), key = k.channel(h / (q.c / k.c));
        const ncnn::Mat value = v.channel(h / (q.c / k.c));
        const ncnn::Mat bias = mask.dims == 3 ? mask.channel(mask.c == 1 ? 0 : h) : mask;
        ncnn::Mat dst = out.channel(h);
        for (int i = 0; i < q.h; i++)
        {
            std::vector<double> scores(k.h);
            for (int j = 0; j < k.h; j++)
            {
                double dot = 0;
                for (int d = 0; d < q.w; d++) dot += double(query.row(i)[d]) * key.row(j)[d];
                scores[j] = dot * 0.5 + bias.row(i)[j];
            }
            const double maximum = *std::max_element(scores.begin(), scores.end());
            double total = 0;
            for (int j = 0; j < k.h; j++) total += scores[j] = std::exp(scores[j] - maximum);
            for (int d = 0; d < v.w; d++)
            {
                double result = 0;
                for (int j = 0; j < k.h; j++) result += scores[j] * value.row(j)[d];
                dst.row(i)[d] = float(result / total);
            }
        }
    }
    return out;
}

static int sdpa_case(ncnn::VulkanDevice* device, int storage, int mask_type)
{
    fprintf(stderr, "RUN SDPA storage=%s mask_type=%d\n", storage_name(storage), mask_type);
    Allocators alloc(device, storage);
    const ncnn::Option& opt = alloc.opt;
    // Odd embedding dimensions force sdpa_cross even when flash attention was
    // compiled. Four query heads / two KV heads also exercise grouped heads.
    ncnn::Mat q(7, 5, 4), k(7, 9, 2), v(5, 9, 2);
    ncnn::Mat mask = mask_type == 0 ? ncnn::Mat(9, 5) : ncnn::Mat(9, 5, mask_type == 1 ? 1 : 4);
    fill(q, 4); fill(k, 5); fill(v, 6); fill(mask, 7);
    const ncnn::Mat expected = attention_reference(q, k, v, mask);
    ncnn::SDPA_vulkan op;
    op.vkdev = device;
    DestroyPipeline cleanup(op, opt);
    ncnn::ParamDict pd;
    pd.set(5, 1);
    pd.set(6, 0.5f);
    if (op.load_param(pd) || op.load_model(ncnn::ModelBinFromMatArray(0)) || op.create_pipeline(opt)) return failure(__func__, __LINE__);
    if (op.use_cooperative_matrix || !valid(op.pipeline_sdpa_qk_cross)
        || !valid(op.pipeline_sdpa_qkv_cross) || !op.qk_softmax) return failure(__func__, __LINE__);
    const ncnn::Softmax_vulkan* softmax = static_cast<ncnn::Softmax_vulkan*>(op.qk_softmax);
    if (!valid(softmax->pipeline_softmax_reduce_max) || !valid(softmax->pipeline_softmax_exp_sub_max)
        || !valid(softmax->pipeline_softmax_reduce_sum) || !valid(softmax->pipeline_softmax_div_sum)) return failure(__func__, __LINE__);
    ncnn::VkCompute cmd(device);
    std::vector<ncnn::VkMat> in(4), out(1);
    if (!upload(cmd, q, in[0], opt, storage) || !upload(cmd, k, in[1], opt, storage)
        || !upload(cmd, v, in[2], opt, storage) || !upload(cmd, mask, in[3], opt, storage)) return failure(__func__, __LINE__);
    const uint64_t before = cmd.pending_dispatch_total();
    if (op.forward(in, out, cmd, opt)) return failure(__func__, __LINE__);
    const uint64_t actual = cmd.pending_dispatch_total() - before;
    // Two cross products plus all four pack1 softmax stages. Its reduction
    // workspaces have shape (query_rows, heads), unlike the 3D score tensor.
    const uint64_t required = workgroups(op.pipeline_sdpa_qk_cross, (k.h + 3) / 4, (q.h + 3) / 4, q.c)
        + workgroups(softmax->pipeline_softmax_reduce_max, q.h, q.c, 1)
        + workgroups(softmax->pipeline_softmax_exp_sub_max, k.h, q.h, q.c)
        + workgroups(softmax->pipeline_softmax_reduce_sum, q.h, q.c, 1)
        + workgroups(softmax->pipeline_softmax_div_sum, k.h, q.h, q.c)
        + workgroups(op.pipeline_sdpa_qkv_cross, (v.w + 3) / 4, (q.h + 3) / 4, q.c);
    if (actual != required)
        fprintf(stderr, "FAIL: expected %llu operator workgroups, observed %llu\n",
                static_cast<unsigned long long>(required), static_cast<unsigned long long>(actual));
    if (actual != required || !actual || out[0].empty() || out[0].elempack != 1
        || out[0].elembits() != (storage ? 16 : 32)) return failure(__func__, __LINE__);
    ncnn::Mat downloaded;
    cmd.record_clone(out[0], downloaded, opt);
    if (cmd.submit_and_wait()) return failure(__func__, __LINE__);
    const int ret = compare_download(expected, downloaded, opt, storage, storage ? 0.002f : 0.0001f);
    fprintf(stderr, "%s SDPA storage=%s mask=%s selected=sdpa_cross dispatch_groups=%llu\n",
            ret ? "FAIL" : "PASS", storage_name(storage), mask_type == 0 ? "2d" : mask_type == 1 ? "singleton-3d" : "per-head-3d",
            static_cast<unsigned long long>(actual));
    return ret;
}

// The public ncnn Vulkan entry points are process-global. This standalone,
// single-threaded test observes them in one bounded RAII scope and forwards
// every call unchanged. No engine source, GPU identity or shader is replaced.
struct ObserveTransfers
{
    static ObserveTransfers* active;
    PFN_vkCreateBuffer create;
    PFN_vkCmdCopyBuffer copy;
    std::map<VkBuffer, VkBufferUsageFlags> usages;
    VkBuffer source;
    unsigned copies;
    ObserveTransfers() : create(ncnn::vkCreateBuffer), copy(ncnn::vkCmdCopyBuffer), source(VK_NULL_HANDLE), copies(0)
    {
        active = this;
        ncnn::vkCreateBuffer = observed_create;
        ncnn::vkCmdCopyBuffer = observed_copy;
    }
    ~ObserveTransfers()
    {
        ncnn::vkCreateBuffer = create;
        ncnn::vkCmdCopyBuffer = copy;
        active = 0;
    }
    static VKAPI_ATTR VkResult VKAPI_CALL observed_create(VkDevice device, const VkBufferCreateInfo* info,
                                                         const VkAllocationCallbacks* allocator, VkBuffer* buffer)
    {
        const VkResult ret = active->create(device, info, allocator, buffer);
        if (ret == VK_SUCCESS) active->usages[*buffer] = info->usage;
        return ret;
    }
    static VKAPI_ATTR void VKAPI_CALL observed_copy(VkCommandBuffer command, VkBuffer src, VkBuffer dst,
                                                   uint32_t count, const VkBufferCopy* regions)
    {
        if (src == active->source && src != dst && count && regions[0].size) active->copies++;
        active->copy(command, src, dst, count, regions);
    }
};
ObserveTransfers* ObserveTransfers::active = 0;

static int memory_case(ncnn::VulkanDevice* device, int storage, int channels)
{
    fprintf(stderr, "RUN MemoryData storage=%s channels=%d\n", storage_name(storage), channels);
    Allocators alloc(device, storage);
    const ncnn::Option& opt = alloc.opt;
    ObserveTransfers observed;
    ncnn::VkWeightAllocator weight_allocator(device);
    ncnn::Option weight_opt = opt;
    weight_opt.blob_vkallocator = &weight_allocator;
    ncnn::Mat input(7, 5, channels);
    fill(input, 8);
    ncnn::MemoryData_vulkan op;
    op.vkdev = device;
    DestroyPipeline cleanup(op, opt);
    ncnn::ParamDict pd;
    pd.set(0, input.w); pd.set(1, input.h); pd.set(2, input.c);
    if (op.load_param(pd) || op.load_model(ncnn::ModelBinFromMatArray(&input)) || op.create_pipeline(opt)) return failure(__func__, __LINE__);
    ncnn::VkTransfer transfer(device);
    if (op.upload_model(transfer, weight_opt) || !transfer.pending_upload_total() || transfer.submit_and_wait()) return failure(__func__, __LINE__);
    const int pack = channels % 4 == 0 ? 4 : 1;
    if (op.data_gpu.empty() || op.data_gpu.allocator != &weight_allocator || op.data_gpu.elempack != pack
        || op.data_gpu.elembits() != (storage ? 16 : 32)) return failure(__func__, __LINE__);
    observed.source = op.data_gpu.buffer();
    const VkBufferUsageFlags required = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT
        | VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT;
    if ((observed.usages[observed.source] & required) != required)
    {
        fprintf(stderr, "FAIL: actual VkWeightAllocator buffer lacks STORAGE/TRANSFER_SRC/TRANSFER_DST usage\n");
        return failure(__func__, __LINE__);
    }
    ncnn::VkCompute cmd(device);
    std::vector<ncnn::VkMat> in, out(1);
    if (op.forward(in, out, cmd, opt) || out[0].empty() || out[0].data == op.data_gpu.data
        || out[0].allocator != opt.blob_vkallocator || out[0].elempack != pack
        || out[0].elembits() != (storage ? 16 : 32)) return failure(__func__, __LINE__);
    ncnn::Mat downloaded;
    cmd.record_clone(out[0], downloaded, opt);
    // Copy-only work must not be mislabeled a compute dispatch. Delayed copy
    // records also reach the observer during submit on non-push-descriptor GPUs.
    if (cmd.pending_dispatch_total() || cmd.submit_and_wait() || observed.copies != 1) return failure(__func__, __LINE__);
    const int ret = compare_download(input, downloaded, opt, storage, 0.f);
    fprintf(stderr, "%s MemoryData storage=%s pack=%d weight_usage=0x%x actual_weight_buffer_copies=%u\n",
            ret ? "FAIL" : "PASS", storage_name(storage), pack, observed.usages[observed.source], observed.copies);
    return ret;
}

int main()
{
    if (ncnn::create_gpu_instance() != 0 || ncnn::get_gpu_count() == 0)
    {
        fprintf(stderr, "FAIL: a working Vulkan device is required; CPU-only/skipped tests do not count\n");
        return failure(__func__, __LINE__);
    }
    ncnn::VulkanDevice* device = ncnn::get_gpu_device();
    if (!device) return failure(__func__, __LINE__);
    const ncnn::GpuInfo& info = device->info;
    fprintf(stderr, "Vulkan device=%s driver=%s vendor=0x%x native_fp16_storage=%d gemm_subgroup=%d\n",
            info.device_name(), info.driver_name(), info.vendor_id(), info.support_fp16_storage(), gemm_subgroup_supported(info));
    if (std::getenv("AGENT_REQUIRE_LAVAPIPE")
        && (info.type() != 3 || (!std::strstr(info.device_name(), "llvmpipe") && !std::strstr(info.device_name(), "lavapipe"))
            || !info.support_fp16_storage() || !gemm_subgroup_supported(info)))
    {
        fprintf(stderr, "FAIL: CI requires lavapipe, native FP16 storage and GEMM subgroup support\n");
        return failure(__func__, __LINE__);
    }
    if (info.vendor_id() != 0x5143)
        fprintf(stderr, "NOT COVERED: Qualcomm/Adreno vendor-conditional packed-FP16 and INT8 cooperative-matrix fixes\n");
    if (!info.support_fp16_storage()) fprintf(stderr, "UNSUPPORTED: native FP16 storage; packed FP16 remains mandatory\n");
    if (!gemm_subgroup_supported(info)) fprintf(stderr, "UNSUPPORTED: GEMM subgroup; requested-on verifies capability fallback\n");
    int cases = 0;
    for (int storage = 0; storage <= (info.support_fp16_storage() ? 2 : 1); storage++)
    {
        for (int subgroup = 0; subgroup < 2; subgroup++)
            for (int transpose = 0; transpose < 2; transpose++)
            {
                if (gemm_case(device, storage, subgroup != 0, 8, 12, 7, transpose != 0)
                    || gemm_case(device, storage, subgroup != 0, 7, 9, 5, transpose != 0)) return failure(__func__, __LINE__);
                cases += 2;
            }
        for (int mask = 0; mask < 3; mask++)
        {
            if (sdpa_case(device, storage, mask)) return failure(__func__, __LINE__);
            cases++;
        }
        if (memory_case(device, storage, 3) || memory_case(device, storage, 4)) return failure(__func__, __LINE__);
        cases += 2;
    }
    fprintf(stderr, "PASS: %d direct Vulkan matrix/attention/copy cases; no physical Adreno or model-inference claim\n", cases);
    return 0;
}
