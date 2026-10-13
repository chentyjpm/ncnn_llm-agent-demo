// Synthetic numerical kernel regression; this is not real model inference.
// Exercise the exact tagged-weight reader used by Embed::load_model, followed
// by the shared head with FP32 extractor-like inputs and low-precision options.
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include <datareader.h>
#include <modelbin.h>
#include "kernel/lm_head.h"

namespace fs = std::filesystem;
constexpr int hidden_size = 13;
constexpr int vocab_size = 11;

static void write_le(std::ostream& stream, uint32_t value, int bytes) {
    for (int i = 0; i < bytes; ++i) stream.put(static_cast<char>((value >> (8 * i)) & 255));
}

static ncnn::Mat fixture_weights(const fs::path& path, bool bf16, std::vector<float>& expected) {
    std::ofstream file(path, std::ios::binary);
    write_le(file, bf16 ? 0x01348B83u : 0u, 4);
    for (int i = 0; i < hidden_size * vocab_size; ++i) {
        const float value = static_cast<float>(i % 17 - 8) / 8.f + static_cast<float>(i % 3) / 32.f;
        uint32_t bits;
        std::memcpy(&bits, &value, sizeof(bits));
        expected.push_back(value); // These values are represented exactly in BF16.
        write_le(file, bf16 ? bits >> 16 : bits, bf16 ? 2 : 4);
    }
    if (bf16 && expected.size() % 2) write_le(file, 0, 2); // ncnn aligns tagged data to four bytes.
    file.close();
    if (!file) throw std::runtime_error("Cannot write synthetic weights");

    FILE* fp = std::fopen(path.string().c_str(), "rb");
    if (!fp) throw std::runtime_error("Cannot read synthetic weights");
    ncnn::Mat weights;
    {
        ncnn::DataReaderFromStdio reader(fp);
        ncnn::ModelBinFromDataReader model_bin(reader);
        weights = model_bin.load(hidden_size * vocab_size, 0);
    }
    std::fclose(fp);
    if (weights.empty() || weights.elembits() != 32 || weights.elempack != 1)
        throw std::runtime_error("Tagged embedding weights must decode to unpacked FP32");
    return weights;
}

static bool check_head(const fs::path& param, const ncnn::Mat& weights,
                       const std::vector<float>& reference, int mode, int rows) {
    ncnn::Option opt;
    opt.num_threads = 1;
    opt.use_fp16_storage = mode != 1;
    opt.use_fp16_arithmetic = true;
    opt.use_fp16_packed = true;
    opt.use_bf16_storage = mode != 0;
    ncnn_llm::LlmHead head;
    if (head.init_shared(param.string(), weights, opt) != 0 || !head.is_shared()) return false;
    ncnn::Mat input(hidden_size, rows);
    for (int r = 0; r < rows; ++r) {
        float* row = input.row(r);
        for (int k = 0; k < hidden_size; ++k) row[k] = static_cast<float>((r * 7 + k) % 9 - 4) / 7.f;
    }
    // One row hits the x86 dispatcher; two rows exercise the shared bare Gemm
    // even on x86. Both use the same path on ARM. Repeat to catch state damage.
    for (int repeat = 0; repeat < 2; ++repeat) {
        const ncnn::Mat actual = head.forward(input, opt);
        if (actual.empty() || actual.w != vocab_size || actual.h != rows ||
            actual.elembits() != 32 || actual.elempack != 1) return false;
        for (int r = 0; r < rows; ++r) {
            const float* row = actual.row(r);
            const float* source = input.row(r);
            for (int n = 0; n < vocab_size; ++n) {
                float expected = 0.f;
                for (int k = 0; k < hidden_size; ++k) expected += source[k] * reference[n * hidden_size + k];
                if (!std::isfinite(row[n]) || std::fabs(row[n] - expected) > 0.0001f * (1.f + std::fabs(expected))) return false;
            }
        }
    }
    return true;
}

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    const fs::path root(argv[1]);
    int failures = 0;
    int cases = 0;
    try {
        fs::create_directories(root);
        const fs::path param = root / "synthetic_head.param";
        std::ofstream file(param);
        file << "7767517\n2 2\nInput input 0 1 in0\nGemm head 1 1 in0 out0 3=1 5=1 6=1 8="
             << vocab_size << " 9=" << hidden_size << " 10=-1\n";
        file.close();
        if (!file) throw std::runtime_error("Cannot write synthetic head graph");
        for (bool bf16 : {false, true}) {
            std::vector<float> reference;
            const ncnn::Mat weights = fixture_weights(root / (bf16 ? "bf16.bin" : "fp32.bin"), bf16, reference);
            for (int mode = 0; mode < 3; ++mode) {
                for (int rows : {1, 2}) {
                    ++cases;
                    if (!check_head(param, weights, reference, mode, rows)) {
                        ++failures;
                        std::cerr << "Shared-head mismatch: bf16_file=" << bf16 << " option_mode=" << mode << " rows=" << rows << '\n';
                    }
                }
            }
        }
    } catch (const std::exception& e) {
        ++failures;
        std::cerr << e.what() << '\n';
    }
    std::cout << "Synthetic shared-head cases: " << cases << "; failures: " << failures << '\n';
    return failures || cases != 12 ? 1 : 0;
}
