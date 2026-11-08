// Reuse the pinned upstream tests without their earlier/later short circuits.
// This supplements, and never replaces, the full upstream test_net target.
#define main upstream_test_net_main
#include "test_net.cpp"
#undef main

int main()
{
    SRAND(7767517);
#if !NCNN_BF16
    fprintf(stderr, "CONFIGURATION_FAILURE: BF16 must be enabled\n");
    return 1;
#else
    const int required[] = {ncnn::LayerType::Input, ncnn::LayerType::Cast,
        ncnn::LayerType::Packing, ncnn::LayerType::ReLU, ncnn::LayerType::UnaryOp,
        ncnn::LayerType::Sigmoid, ncnn::LayerType::Convolution, ncnn::LayerType::Padding};
    for (size_t i = 0; i < sizeof(required) / sizeof(required[0]); i++)
    {
        if (!layer_available(required[i]))
        {
            fprintf(stderr, "CONFIGURATION_FAILURE: required builtin layer %d missing\n", required[i]);
            return 1;
        }
    }
    // The custom layer rejects a wrong storage type before using its bytes.
    // Stop here on regression: old builtin kernels may otherwise interpret a
    // BF16 allocation as FP32 and corrupt memory instead of failing cleanly.
    const int custom = test_storage_featmask();
    if (custom)
    {
        fprintf(stderr, "STORAGE_FEATMASK_REGRESSION: custom=%d; builtin and packing not run\n", custom);
        return 2;
    }
    const int builtin = test_storage_featmask_builtin();
    const int packing = test_storage_packing(4);
    if (custom || builtin || packing)
    {
        fprintf(stderr, "STORAGE_FEATMASK_REGRESSION: custom=%d builtin=%d packing=%d\n", custom, builtin, packing);
        return 2;
    }
    fprintf(stderr, "PASS storage featmask: 2048 custom network combinations, 72 builtin option cases, BF16 input packing\n");
    fprintf(stderr, "SCOPE: CPU storage consistency; requested FP16 is not proof of ARM/RISC-V FP16 execution; Vulkan requests may fall back to CPU\n");
    return 0;
#endif
}
