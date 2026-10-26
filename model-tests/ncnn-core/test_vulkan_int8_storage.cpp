// Compile the real pinned ncnn GLSL path and inspect its emitted SPIR-V.
// This checks storage representation, not shader execution or model inference.
#include "gpu.h"
#include "option.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <vector>

#if !NCNN_VULKAN
#error This regression requires NCNN_VULKAN
#endif

static int storage_case(bool signed_bytes)
{
    const std::string type = signed_bytes ? "int8_t" : "uint8_t";
    // Do not put the extension or use_storage_buffer pragma in this fixture:
    // both must come from ncnn's use_int8_storage option under test.
    const std::string shader =
        "#version 450\n"
        "layout(local_size_x=1, local_size_y=1, local_size_z=1) in;\n"
        "layout(binding=0) readonly buffer input_block { " + type + " a[]; };\n"
        "layout(binding=1) writeonly buffer output_block { " + type + " b[]; };\n"
        "void main() { uint i=gl_GlobalInvocationID.x; b[i]=a[i]; }\n";
    ncnn::Option opt;
    opt.use_vulkan_compute = true;
    opt.use_fp16_storage = false;
    opt.use_fp16_packed = false;
    opt.use_fp16_arithmetic = false;
    opt.use_bf16_storage = false;
    opt.use_int8_storage = true;
    opt.use_int8_arithmetic = false;
    opt.use_subgroup_ops = false;
    opt.use_cooperative_matrix = false;
    std::vector<uint32_t> spv;
    if (ncnn::compile_spirv_module(shader.data(), (int)shader.size(), opt, spv) != 0
        || spv.size() < 5 || spv[0] != 0x07230203u)
    {
        fprintf(stderr, "FAIL: INT8 storage shader compilation/header\n");
        return 1;
    }

    // SPIR-V core opcodes/storage classes. Inspect the two decorated descriptor
    // variables and their pointer types, rather than just searching raw words.
    const uint32_t OpTypeInt = 21, OpTypePointer = 32, OpVariable = 59, OpDecorate = 71;
    const uint32_t Binding = 33, Uniform = 2, StorageBuffer = 12;
    std::map<uint32_t, uint32_t> pointer_storage, binding, variable_type, variable_storage;
    bool byte_type = false;
    for (size_t i = 5; i < spv.size();)
    {
        const uint32_t words = spv[i] >> 16, op = spv[i] & 0xffff;
        if (!words || words > spv.size() - i)
        {
            fprintf(stderr, "FAIL: malformed SPIR-V instruction\n");
            return 1;
        }
        if (op == OpTypeInt && words == 4 && spv[i + 2] == 8
            && spv[i + 3] == uint32_t(signed_bytes)) byte_type = true;
        if (op == OpTypePointer && words == 4)
            pointer_storage[spv[i + 1]] = spv[i + 2];
        if (op == OpDecorate && words == 4 && spv[i + 2] == Binding)
            binding[spv[i + 1]] = spv[i + 3];
        if (op == OpVariable && words >= 4)
        {
            variable_type[spv[i + 2]] = spv[i + 1];
            variable_storage[spv[i + 2]] = spv[i + 3];
            if (spv[i + 3] == Uniform)
            {
                fprintf(stderr, "FAIL: INT8 buffer emitted obsolete Uniform storage class\n");
                return 1;
            }
        }
        i += words;
    }
    unsigned found = 0;
    for (std::map<uint32_t, uint32_t>::const_iterator it = binding.begin(); it != binding.end(); ++it)
    {
        if (it->second > 1 || !variable_type.count(it->first)
            || variable_storage[it->first] != StorageBuffer
            || !pointer_storage.count(variable_type[it->first])
            || pointer_storage[variable_type[it->first]] != StorageBuffer)
        {
            fprintf(stderr, "FAIL: descriptor binding/type is not StorageBuffer\n");
            return 1;
        }
        if (found & (1u << it->second)) return 1;
        found |= 1u << it->second;
    }
    if (!byte_type || found != 3)
    {
        fprintf(stderr, "FAIL: missing byte type or live input/output descriptors\n");
        return 1;
    }
    fprintf(stderr, "PASS: %s shader has two live StorageBuffer descriptors and no Uniform buffers\n", type.c_str());
    return 0;
}

int main()
{
    if (ncnn::create_gpu_instance() != 0 || ncnn::get_gpu_count() == 0)
    {
        fprintf(stderr, "FAIL: a working Vulkan device is required; no skipped success\n");
        return 1;
    }
    ncnn::VulkanDevice* device = ncnn::get_gpu_device();
    if (!device || !device->info.support_int8_storage())
    {
        fprintf(stderr, "FAIL: INT8 storage feature is required\n");
        return 1;
    }
    const ncnn::GpuInfo& info = device->info;
    if (std::getenv("AGENT_REQUIRE_LAVAPIPE")
        && (info.type() != 3 || (!std::strstr(info.device_name(), "llvmpipe") && !std::strstr(info.device_name(), "lavapipe"))))
    {
        fprintf(stderr, "FAIL: CI requires the Mesa software Vulkan device\n");
        return 1;
    }
    const int result = storage_case(false) || storage_case(true);
    if (!result) fprintf(stderr, "PASS: 2 real GLSL-to-SPIR-V INT8 storage checks; no dispatch/performance claim\n");
    ncnn::destroy_gpu_instance();
    return result;
}
