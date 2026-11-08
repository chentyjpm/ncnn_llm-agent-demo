// Both generic and selected CPU Reduction must independently match finite
// double expectations. Comparing them only to each other can accept inf/NaN.
// This is operator coverage, not trained-model inference.
#include "stability_reference.h"
#include "layer.h"
#include "reduction.h"

int main()
{
    ncnn::Option opt;
    opt.num_threads = 1;
    opt.use_vulkan_compute = false;
    opt.use_packing_layout = false;
    opt.use_fp16_packed = opt.use_fp16_storage = opt.use_fp16_arithmetic = false;
    opt.use_bf16_packed = opt.use_bf16_storage = false;
    int cases = 0, failures = 0;
    for (size_t i = 0; i < sizeof(stability::reduction_cases) / sizeof(stability::reduction_cases[0]); i++)
        for (int pattern = 0; pattern < 3; pattern++)
        {
            const stability::ReductionCase& tc = stability::reduction_cases[i];
            const ncnn::Mat input = stability::reduction_input(tc, pattern);
            if (input.empty()) return stability::failure("input allocation");
            const stability::Reference expected = stability::reduction_reference(tc, input);
            const ncnn::ParamDict pd = stability::reduction_params(tc);
            for (int selected = 0; selected < 2; selected++)
            {
                std::fprintf(stderr, "RUN Reduction op=10 case=%s pattern=%d backend=%s coeff=%.9g keepdims=%d\n",
                             tc.name, pattern, selected ? "selected_cpu" : "generic", tc.coeff, tc.keepdims);
                ncnn::Layer* op = selected ? ncnn::create_layer_cpu("Reduction") : new ncnn::Reduction;
                if (!op) return stability::failure("CPU layer allocation");
                int ret = op->load_param(pd);
                if (!ret) ret = op->create_pipeline(opt);
                ncnn::Mat actual;
                if (!ret) ret = op->forward(input, actual, opt);
                op->destroy_pipeline(opt);
                delete op;
                if (ret) return stability::failure("CPU Reduction load/create/forward");
                ret = stability::compare("Reduction", actual, expected, false);
                if (ret == 1) return 1;
                failures += ret == 2;
                cases++;
            }
        }
    std::fprintf(stderr, "%s: %d CPU LogSumExp cases; numerical_failures=%d; no model inference\n", failures ? "FAIL" : "PASS", cases, failures);
    return failures ? 2 : 0;
}
