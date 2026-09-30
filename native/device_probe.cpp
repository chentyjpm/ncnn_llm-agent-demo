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
    int requested = -1;
    const bool enumerate = argc == 1 || (argc == 2 && std::string(argv[1]) == "--enumerate");
    if (!enumerate) {
        if (argc != 3 || std::string(argv[1]) != "--check-device") {
            std::fputs("Usage: ncnn_device_probe [--enumerate | --check-device N]\n", stderr); return 2;
        }
        const std::string id = argv[2];
        if (id.empty() || id.size() > 2 || id.find_first_not_of("0123456789") != std::string::npos) {
            std::fputs("Invalid device index\n", stderr); return 2;
        }
        requested = 0;
        for (char c : id) requested = requested * 10 + (c - '0');
        if (requested >= 16) { std::fputs("Invalid device index\n", stderr); return 2; }
    }
    std::ostringstream out;
    out << "{\"version\":1,\"phase\":" << quoted(enumerate ? "enumerate" : "compute")
        << ",\"compiled\":" << (NCNN_VULKAN ? "true" : "false") << ",\"devices\":[";
#if NCNN_VULKAN
    const int init = ncnn::create_gpu_instance();
    const int count = init == 0 ? std::min(16, ncnn::get_gpu_count()) : 0;
    if (!enumerate && requested >= count) {
        ncnn::destroy_gpu_instance();
        std::fputs("Requested device unavailable\n", stderr); return 2;
    }
    bool first = true;
    for (int i = 0; i < count; ++i) {
        if (!enumerate && i != requested) continue;
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
        // Enumeration NEVER creates a logical VulkanDevice or claims compute.
        // The host invokes --check-device in a separate bounded process. Driver
        // crashes stay observable failures for that one device, not fake passes.
        const bool ok = !enumerate && compute(i);
        if (!first) out << ',';
        first = false;
        out << "{\"id\":" << i << ",\"name\":" << quoted(name) << ",\"type\":" << quoted(type)
            << ",\"hardware\":" << (hardware ? "true" : "false") << ",\"compute_ok\":" << (ok ? "true" : "false") << '}';
    }
    ncnn::destroy_gpu_instance();
#else
    if (!enumerate) { std::fputs("Vulkan not compiled\n", stderr); return 2; }
#endif
    out << "]}";
    std::puts(out.str().c_str());
    return 0;
}
