// CPU-only fixture/oracle self-check. Passing this does NOT validate a Vulkan
// shader, a dispatch, a device or inference. The strict companion does those
// operator checks and cannot substitute this program for missing Vulkan.
#include "reduction_fp32_accum_reference.h"

static ncnn::Mat expected_shape(size_t index)
{
    switch (index)
    {
    case 0: return ncnn::Mat(1);
    case 1: return ncnn::Mat(3);
    case 2: return ncnn::Mat(5, 1);
    case 3: return ncnn::Mat(1, 1, 3);
    case 4: return ncnn::Mat(5, 3);
    case 5: return ncnn::Mat(3, 3);
    case 6: return ncnn::Mat(5, 3, 3);
    default: return ncnn::Mat(1, 1, 1, 1);
    }
}

int main()
{
    ncnn::Option opt;
    opt.num_threads = 1;
    opt.use_vulkan_compute = false;
    opt.use_packing_layout = false;
    static const int reduced_sizes[] = {4097, 257, 257, 289, 257, 289, 257, 459};
    int fixtures = 0, uniform_cases = 0;
    for (size_t i = 0; i < sizeof(accumulation::cases) / sizeof(accumulation::cases[0]); i++)
    {
        const stability::ReductionCase& tc = accumulation::cases[i];
        for (size_t o = 0; o < sizeof(accumulation::operations) / sizeof(accumulation::operations[0]); o++)
        {
            const int op = accumulation::operations[o];
            for (int pattern = 0; pattern < 2; pattern++)
            {
                const ncnn::Mat input = accumulation::input(tc, op, pattern);
                if (input.empty()) return stability::failure("self-check input allocation");
                const accumulation::Reference result = accumulation::reference(tc, input, op);
                if (!accumulation::valid_fixture(input, result)
                    || !stability::same_layout(result.expected.layout, expected_shape(i)))
                    return stability::failure("self-check finite adversarial fixture/independent shape");
                for (size_t j = 0; j < result.sizes.size(); j++)
                    if (result.sizes[j] != reduced_sizes[i]) return stability::failure("self-check reduced size");
                for (int half = 0; half < 2; half++)
                {
                    ncnn::Mat storage;
                    if (!accumulation::exact_storage(input, half != 0, storage, opt))
                        return stability::failure("self-check finite exact FP32/FP16 storage");
                }
                std::fprintf(stderr, "PASS FIXTURE op=%d name=%s case=%s pattern=%d outputs=%d reduced_size=%d min_accumulator=%.17g max_accumulator=%.17g\n",
                             op, accumulation::operation_name(op), tc.name, pattern, stability::count(result.expected.layout), reduced_sizes[i],
                             *std::min_element(result.accumulators.begin(), result.accumulators.end()),
                             *std::max_element(result.accumulators.begin(), result.accumulators.end()));
                fixtures++;
            }
            // Closed-form uniform expectations check all seven finalizers,
            // signed inputs, coefficient order, axes, keepdims and padding.
            ncnn::Mat uniform = stability::shape(stability::extents(tc));
            const double x = op == 1 || op == 2 || op == 7 || op == 8 ? -256. : 8192.;
            uniform.fill(float(x));
            const accumulation::Reference result = accumulation::reference(tc, uniform, op);
            double e = (op == 1 || op == 7 ? std::fabs(x) : op == 2 || op == 8 ? x * x : x) * reduced_sizes[i];
            if (op == 3) e = x;
            if (op == 8) e = std::fabs(x) * std::sqrt(double(reduced_sizes[i]));
            if (op == 9) e = std::log(x) + std::log(double(reduced_sizes[i]));
            e *= accumulation::coefficient(tc, op);
            if (!stability::same_layout(result.expected.layout, expected_shape(i))) return stability::failure("self-check uniform shape");
            for (size_t j = 0; j < result.expected.values.size(); j++)
                if (!std::isfinite(result.expected.values[j]) || std::fabs(result.expected.values[j] - e) > 1.e-10 * std::max(1., std::fabs(e)))
                    return stability::failure("self-check independent closed-form oracle");
            uniform_cases++;
        }
    }
    // Fixture validation must reject bad inputs and an oracle that no longer
    // tests FP16 overflow. A failed conversion is infrastructure, not evidence
    // that the Reduction shader has a numerical regression.
    ncnn::Mat guarded_input = accumulation::input(accumulation::cases[0], 3, 0);
    accumulation::Reference guarded = accumulation::reference(accumulation::cases[0], guarded_input, 3);
    const float original = stability::value(guarded_input, 0);
    stability::value(guarded_input, 0) = std::numeric_limits<float>::infinity();
    ncnn::Mat storage;
    if (accumulation::valid_fixture(guarded_input, guarded)
        || accumulation::exact_storage(guarded_input, true, storage, opt)) return stability::failure("self-check must reject infinite input");
    stability::value(guarded_input, 0) = 65536.f;
    if (accumulation::valid_fixture(guarded_input, guarded)
        || accumulation::exact_storage(guarded_input, true, storage, opt)) return stability::failure("self-check must reject overflowing FP16 input conversion");
    stability::value(guarded_input, 0) = original;
    const double old_accumulator = guarded.accumulators[0];
    guarded.accumulators[0] = 65504.;
    if (accumulation::valid_fixture(guarded_input, guarded)) return stability::failure("self-check must require adversarial accumulation");
    guarded.accumulators[0] = old_accumulator;
    guarded.expected.values[0] = std::numeric_limits<double>::infinity();
    if (accumulation::valid_fixture(guarded_input, guarded)) return stability::failure("self-check must reject infinite oracle");
    // Contract sentinels are deliberately rejected. In the strict companion
    // compare() is called only after successful Vulkan submit and readback.
    stability::Reference reference;
    reference.layout = ncnn::Mat(1);
    reference.values.push_back(1.);
    ncnn::Mat actual(1);
    actual.fill(1.f);
    if (stability::compare("selfcheck_finite", actual, reference, false) != 0) return 1;
    std::fprintf(stderr, "SELFTEST deliberate mismatch/infra sentinels begin\n");
    actual.fill(std::numeric_limits<float>::infinity());
    if (stability::compare("selfcheck_infinity", actual, reference, true) != 2) return 1;
    actual.fill(std::numeric_limits<float>::quiet_NaN());
    if (stability::compare("selfcheck_nan", actual, reference, true) != 2) return 1;
    actual.fill(2.f);
    if (stability::compare("selfcheck_wrong_finite", actual, reference, false) != 2) return 1;
    reference.values[0] = std::numeric_limits<double>::infinity();
    if (stability::compare("selfcheck_bad_reference", actual, reference, false) != 1) return 1;
    std::fprintf(stderr, "SELFTEST deliberate mismatch/infra sentinels end\n");
    std::fprintf(stderr, "PASS CPU-only oracle: %d adversarial fixtures, %d closed-form cases, exact finite FP32/FP16 storage and exit-code sentinels; GPU_NOT_RUN\n", fixtures, uniform_cases);
    return 0;
}
