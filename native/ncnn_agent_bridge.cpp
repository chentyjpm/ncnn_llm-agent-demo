// Original adapter for futz12/ncnn_llm. Native compilation/inference must be validated locally.
// JSON-RPC stdio protocol is separate from MCP; all tool execution stays in Python.
#include <algorithm>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#include <nlohmann/json.hpp>
#include "ncnn_llm_gpt.h"
#include "utils/prompt.h"
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#define DUP _dup
#define DUP2 _dup2
#define FILENO _fileno
#define FDOPEN _fdopen
#else
#include <unistd.h>
#define DUP dup
#define DUP2 dup2
#define FILENO fileno
#define FDOPEN fdopen
#endif
using J = nlohmann::json;

int main(int argc, char** argv) {
    // Preserve protocol output and redirect ALL library stdout logging to stderr.
    std::fflush(stdout);
    FILE* protocol = FDOPEN(DUP(FILENO(stdout)), "wb");
    if (!protocol || DUP2(FILENO(stderr), FILENO(stdout)) < 0) return 2;
#ifdef _WIN32
    _setmode(FILENO(stdin), _O_BINARY);
#endif
    auto send = [&](const J& msg) {
        const std::string s = msg.dump() + "\n";
        std::fwrite(s.data(), 1, s.size(), protocol);
        std::fflush(protocol);
    };
    try {
        std::string model_path;
        int threads = 4, device = 0;
        bool vulkan = false;
        for (int i = 1; i < argc; ++i) {
            const std::string a = argv[i];
            if (a == "--vulkan") vulkan = true;
            else if (a == "--model" && i + 1 < argc) model_path = argv[++i];
            else if (a == "--threads" && i + 1 < argc) threads = std::stoi(argv[++i]);
            else if (a == "--vulkan-device" && i + 1 < argc) device = std::stoi(argv[++i]);
            else throw std::runtime_error("Unsupported/incomplete bridge argument: " + a);
        }
        if (model_path.empty() || !std::filesystem::is_regular_file(std::filesystem::path(model_path) / "model.json"))
            throw std::runtime_error("--model must point to a converted ncnn model directory");
        if (threads < 1 || threads > 128 || device < 0) throw std::runtime_error("Invalid threads/device");
        TemplateType template_type = TemplateType::CHATML;
        {
            std::ifstream config_file(std::filesystem::path(model_path) / "model.json");
            J config; config_file >> config;
            if (config.value("type", std::string()) == "youtu_llm") template_type = TemplateType::YOUTU;
        }
        // Keep weights resident; rebuild a bounded conversation KV cache for each request.
        ncnn_llm_gpt model(model_path, vulkan, threads, device, true);
        std::string line;
        while (std::getline(std::cin, line)) {
            J id = nullptr;
            try {
                if (line.size() > 1048576) throw std::runtime_error("Request exceeds 1 MiB");
                const J req = J::parse(line);
                if (!req.is_object() || req.value("jsonrpc", std::string()) != "2.0")
                    throw std::runtime_error("Expected JSON-RPC 2.0");
                if (!req.contains("id")) continue;
                id = req.at("id");
                const auto method = req.value("method", std::string());
                if (method == "ping") { send({{"jsonrpc", "2.0"}, {"id", id}, {"result", J::object()}}); continue; }
                if (method != "infer") {
                    send({{"jsonrpc", "2.0"}, {"id", id}, {"error", {{"code", -32601}, {"message", "Unknown method"}}}});
                    continue;
                }
                const J p = req.at("params");
                const int max_tokens = p.value("max_new_tokens", 1024);
                if (max_tokens < 1 || max_tokens > 8192) throw std::runtime_error("Invalid max_new_tokens");
                const auto& source_messages = p.at("messages");
                if (!source_messages.is_array() || source_messages.size() > 256) throw std::runtime_error("Invalid message list");
                std::vector<Message> messages;
                for (const auto& m : source_messages) {
                    auto role = m.at("role").get<std::string>();
                    if (role != "system" && role != "user" && role != "assistant") throw std::runtime_error("Invalid role");
                    messages.emplace_back(role, m.at("content").get<std::string>());
                }
                const auto prompt = apply_chat_template(template_type, messages, {}, true, false);
                auto ctx = model.prefill(prompt);
                GenerateConfig cfg;
                cfg.max_new_tokens = max_tokens;
                cfg.do_sample = 0;
                cfg.top_k = 40;
                cfg.top_p = 0.9f;
                cfg.temperature = 0.3f;
                // JSON action prompting, NOT direct native tool dispatch. Native tools are not registered.
                std::string text;
                ctx = model.generate(ctx, cfg, [&](const std::string& token) { text += token; });
                send({{"jsonrpc", "2.0"}, {"id", id}, {"result", {{"text", text}}}});
            } catch (const std::exception& e) {
                send({{"jsonrpc", "2.0"}, {"id", id}, {"error", {{"code", -32000}, {"message", e.what()}}}});
            }
        }
    } catch (const std::exception& e) {
        std::fprintf(stderr, "ncnn_agent_bridge: %s\n", e.what());
        std::fclose(protocol);
        return 2;
    }
    std::fclose(protocol);
    return 0;
}
