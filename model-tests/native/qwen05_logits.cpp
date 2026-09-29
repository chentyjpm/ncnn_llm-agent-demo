// Numerical validation of the exported real model, including incremental KV decoding.
#include "ncnn_text_runtime.h"
#include "utils/rope_embed.h"
#include <nlohmann/json.hpp>
#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
using J = nlohmann::json;

static void load(ncnn::Net& net, const std::string& param, const std::string& bin) {
    net.opt.num_threads = 4;
    net.opt.use_vulkan_compute = false;
    net.opt.use_bf16_storage = false;
    net.opt.use_fp16_storage = false;
    net.opt.use_fp16_packed = false;
    net.opt.use_fp16_arithmetic = false;
    if (net.load_param(param.c_str()) || net.load_model(bin.c_str())) throw std::runtime_error("ncnn model load failed");
}
int main(int argc, char** argv) {
    J report = {{"backend", "ncnn CPU FP32"}, {"ok", false}, {"cases", J::array()}};
    if (argc != 4) { std::cerr << "usage: qwen05_logits model_dir reference.json report.json\n"; return 2; }
    try {
        const std::string dir = argv[1];
        ncnn::Net embed, decoder, head;
        load(embed, dir + "/embed.param", dir + "/embed.bin");
        load(decoder, dir + "/decoder.param", dir + "/decoder.bin");
        load(head, dir + "/head.param", dir + "/embed.bin");
        J refs; std::ifstream(argv[2]) >> refs;
        bool ok = true;
        for (const auto& test : refs.at("vectors")) {
            auto ids = test.at("input_ids").get<std::vector<int>>();
            ncnn::UnlockedPoolAllocator allocator;
            KVCache cache;
            int position = 0;
            J results = J::array();
            for (const auto& step : test.at("steps")) {
                const int len = static_cast<int>(ids.size());
                ncnn::Mat mask(position + len, len), cos, sin;
                for (int i = 0; i < len; ++i)
                    for (int j = 0; j < position + len; ++j)
                        mask.row(i)[j] = j > position + i ? -INFINITY : 0.0f;
                generate_rope_embed_cache(len, 64, position, cos, sin, 1000000.f);
                auto embeddings = llm_run_text_embed(embed, ids);
                if (embeddings.empty()) throw std::runtime_error("empty embeddings");
                auto hidden = llm_run_decoder_with_kv(decoder, embeddings, mask, cos, sin, cache, 24, position == 0, &allocator, 512);
                if (hidden.empty() || hidden.w != 896) throw std::runtime_error("invalid decoder output");
                ncnn::Mat last(hidden.w, 1, (void*)hidden.row(hidden.h - 1));
                auto logits = llm_run_lm_head(head, last);
                if (logits.empty() || logits.w != 151936) throw std::runtime_error("invalid logits shape");
                const float* data = logits;
                const int next = static_cast<int>(std::max_element(data, data + logits.w) - data);
                float error = 0;
                bool finite = true;
                for (int j = 0; j < logits.w; ++j) finite = finite && std::isfinite(data[j]);
                for (size_t j = 0; j < step["indices"].size(); ++j) {
                    const int idx = step["indices"][j];
                    error = std::max(error, std::abs(data[idx] - step["logits"][j].get<float>()));
                }
                bool pass = finite && next == step["next_id"].get<int>() && error < 0.04f;
                ok = ok && pass;
                results.push_back({{"position", position}, {"next_id", next}, {"expected", step["next_id"]},
                                   {"max_abs_error_top8", error}, {"finite", finite}, {"passed", pass}});
                position += len;
                ids = {step["next_id"].get<int>()}; // teacher-forced comparison of the SAME next prefix
            }
            report["cases"].push_back({{"question", test["question"]}, {"steps", results}});
            cache.clear();
        }
        report["ok"] = ok;
    } catch (const std::exception& e) { report["error"] = e.what(); }
    std::ofstream(argv[3]) << report.dump(2) << '\n';
    std::cout << report.dump(2) << std::endl;
    return report["ok"].get<bool>() ? 0 : 1;
}
