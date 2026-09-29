// Built separately against EACH engine's ncnn. No model weights are loaded.
// A successful device result requires an actual Vulkan MAX(x, 0) dispatch/download.
#include <algorithm>
#include <cmath>
#include <cctype>
#include <cstdio>
#include <sstream>
#include <string>
#include "net.h"
#include "gpu.h"
#include "command.h"
#include "layer.h"

static std::string quoted(const std::string& s) {
    std::string out = "\"";
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') { out += '\\'; out += c; }
        else if (c < 32) { char buf[7]; std::snprintf(buf, sizeof(buf), "\\u%04x", c); out += buf; }
        else out += c;
    }
    return out + '"';
}
#if NCNN_VULKAN
static bool compute(int index) {
    const ncnn::VulkanDevice* device = ncnn::get_gpu_device(index);
    if (!device) return false;
    ncnn::VkBlobAllocator blob(device);
    ncnn::VkStagingAllocator staging(device);
    ncnn::Net net;
    net.set_vulkan_device(index);
    net.opt.use_vulkan_compute = true;
    net.opt.use_packing_layout = false;
    net.opt.use_fp16_packed = false;
    net.opt.use_fp16_storage = false;
    net.opt.use_fp16_arithmetic = false;
    net.opt.use_bf16_storage = false;
    net.opt.blob_vkallocator = &blob;
    net.opt.workspace_vkallocator = &blob;
    net.opt.staging_vkallocator = &staging;
    // Qwen Image's reduced ncnn build omits ReLU but includes BinaryOp.
    // MAX(x, 0) is exactly ReLU: keep the same nontrivial numerical acceptance
    // and require an actual VkMat result; never accept a CPU-only extraction.
    if (net.load_param_mem("7767517\n2 2\nInput input 0 1 in\nBinaryOp relu 1 1 in out 0=4 1=1 2=0\n") != 0) return false;
    static const unsigned int weights[1] = {0};
    if (net.load_model(reinterpret_cast<const unsigned char*>(weights)) != 0) return false;
    if (!net.opt.use_vulkan_compute || net.layers().size() != 2 || !net.layers()[1]->support_vulkan) return false;
    ncnn::Mat input(8), output;
    for (int i = 0; i < 8; ++i) input[i] = float(i - 4);
    ncnn::VkMat gpu_output;
    ncnn::Extractor ex = net.create_extractor();
    ncnn::VkCompute cmd(device);
    if (ex.input("in", input) != 0 || ex.extract("out", gpu_output, cmd) != 0 || gpu_output.empty()) return false;
    cmd.record_download(gpu_output, output, net.opt);
    if (cmd.submit_and_wait() != 0 || output.total() != 8) return false;
    for (int i = 0; i < 8; ++i)
        if (!std::isfinite(output[i]) || std::abs(output[i] - std::max(0.f, float(i - 4))) > 1e-6f) return false;
    return true;
}
#endif
int main(int argc, char** argv) {
    const bool software = argc == 2 && std::string(argv[1]) == "--include-software";
    if (argc > 1 && !software) { std::fputs("Usage: ncnn_device_probe [--include-software]\n", stderr); return 2; }
    std::ostringstream out;
    out << "{\"version\":1,\"compiled\":" << (NCNN_VULKAN ? "true" : "false") << ",\"devices\":[";
#if NCNN_VULKAN
    const int init = ncnn::create_gpu_instance();
    const int count = init == 0 ? std::min(16, ncnn::get_gpu_count()) : 0;
    for (int i = 0; i < count; ++i) {
        const auto& props = ncnn::get_gpu_info(i).physicalDeviceProperties();
        std::string name = props.deviceName, lower = name;
        std::transform(lower.begin(), lower.end(), lower.begin(), [](unsigned char c){ return char(std::tolower(c)); });
        const bool hardware = props.deviceType != VK_PHYSICAL_DEVICE_TYPE_CPU &&
            lower.find("llvmpipe") == std::string::npos && lower.find("lavapipe") == std::string::npos &&
            lower.find("swiftshader") == std::string::npos;
        const char* type = props.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU ? "discrete" :
            props.deviceType == VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU ? "integrated" :
            props.deviceType == VK_PHYSICAL_DEVICE_TYPE_VIRTUAL_GPU ? "virtual" :
            props.deviceType == VK_PHYSICAL_DEVICE_TYPE_CPU ? "cpu" : "other";
        bool ok = false;
        // ncnn may export -fno-exceptions; process isolation handles fatal driver errors.
        if (hardware || software) ok = compute(i);
        if (i) out << ',';
        out << "{\"id\":" << i << ",\"name\":" << quoted(name) << ",\"type\":" << quoted(type)
            << ",\"hardware\":" << (hardware ? "true" : "false") << ",\"compute_ok\":" << (ok ? "true" : "false") << '}';
    }
    ncnn::destroy_gpu_instance();
#endif
    out << "]}";
    std::puts(out.str().c_str());
    return 0;
}
