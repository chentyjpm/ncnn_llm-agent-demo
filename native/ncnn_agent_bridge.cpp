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
#include "utils/stb_image.h"
#include "cli_options.h"
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
#if defined(NCNN_LLM_MINICPM_V47_RUNTIME) && NCNN_LLM_MINICPM_V47_RUNTIME >= 1
static constexpr int minicpm_runtime_version = NCNN_LLM_MINICPM_V47_RUNTIME;
#else
static constexpr int minicpm_runtime_version = 0;
#endif

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--capabilities") {
        std::cout << J({{"minicpm_v4_7_runtime", minicpm_runtime_version}, {"single_image_protocol", minicpm_runtime_version >= 1}}).dump() << "\n";
        return 0;
    }
    // Loader/CLI smoke checks must not instantiate a model or initialize a GPU.
    if (argc == 2 && (std::string(argv[1]) == "--help" || std::string(argv[1]) == "-h")) {
        std::puts("Usage: ncnn_agent_bridge --model DIR [--threads N] [--vulkan] [--vulkan-device N]");
        std::puts("JSON-RPC stdio bridge. Model weights are required for inference, not for --help/--version.");
        return 0;
    }
    if (argc == 2 && std::string(argv[1]) == "--version") {
        std::puts("ncnn_agent_bridge 0.1.0");
        return 0;
    }
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
            else if (a == "--threads" && i + 1 < argc) threads = parse_cli_integer(argv[++i], "--threads", 1, 128);
            else if (a == "--vulkan-device" && i + 1 < argc) device = parse_cli_integer(argv[++i], "--vulkan-device", 0, std::numeric_limits<int>::max());
            else throw std::runtime_error("Unsupported/incomplete bridge argument: " + a);
        }
        if (model_path.empty() || !std::filesystem::is_regular_file(std::filesystem::path(model_path) / "model.json"))
            throw std::runtime_error("--model must point to a converted ncnn model directory");
        if (threads < 1 || threads > 128 || device < 0) throw std::runtime_error("Invalid threads/device");
        TemplateType template_type = TemplateType::CHATML;
        bool minicpm_vision = false;
        {
            std::ifstream config_file(std::filesystem::path(model_path) / "model.json");
            J config; config_file >> config;
            minicpm_vision = config.value("setting", J::object()).value("vision", J::object()).value("type", std::string()) == "minicpm_v4_7";
#if defined(NCNN_LLM_MINICPM_V47_RUNTIME) && NCNN_LLM_MINICPM_V47_RUNTIME >= 1
            if (minicpm_vision) template_type = TemplateType::MINICPM_V47;
#endif
            if (config.value("type", std::string()) == "youtu_llm") template_type = TemplateType::YOUTU;
        }
        if (minicpm_vision && minicpm_runtime_version < 1)
            throw std::runtime_error("This bridge was built without MiniCPM-V-4.7 runtime support; rebuild against the reviewed feature source. A model.json entry alone cannot enable vision.");
        // Keep weights resident; rebuild a bounded conversation KV cache for each request.
        ncnn_llm_gpt model(model_path, vulkan, threads, device, !minicpm_vision);
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
                if (method == "capabilities") {
                    send({{"jsonrpc", "2.0"}, {"id", id}, {"result", {{"single_image", minicpm_runtime_version>=1 && minicpm_vision && model.supports_vision() ? "minicpm_v4_7_experimental" : "unsupported"}}}});
                    continue;
                }
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
                std::shared_ptr<ncnn_llm_gpt_ctx> ctx;
                if (p.contains("image_path")) {
                    if (!minicpm_vision || !model.supports_vision()) throw std::runtime_error("Image input unsupported by this model");
                    if (messages.empty() || messages.back().role != "user") throw std::runtime_error("Image must accompany the final user message");
                    for (const auto& m : messages)
                        if (m.content.find("<image>") != std::string::npos) throw std::runtime_error("Image markers are reserved for the image protocol");
                    const auto image_path = p.at("image_path").get<std::string>();
                    const std::filesystem::path path(image_path);
                    if (!path.is_absolute() || std::filesystem::is_symlink(path) || !std::filesystem::is_regular_file(path) || std::filesystem::file_size(path) > 8*1024*1024)
                        throw std::runtime_error("Invalid image file path/size");
                    int image_width = 0, image_height = 0, image_channels = 0;
                    if (!stbi_info(image_path.c_str(), &image_width, &image_height, &image_channels) ||
                        image_width <= 0 || image_height <= 0 || image_width > 16384 || image_height > 16384 ||
                        static_cast<int64_t>(image_width) * image_height > 16000000)
                        throw std::runtime_error("Image dimensions exceed experimental vision limits");
                    const auto image = load_image_to_ncnn_mat(image_path);
                    if (ncnn_mat_empty(image)) throw std::runtime_error("Cannot decode image");
                    if (messages.size()==1) messages.insert(messages.begin(), Message("system", "You are a helpful assistant."));
                    messages.back().content = "<image>\n" + messages.back().content;
                    // Render the whole history first: historical reasoning treatment depends
                    // on the final user turn. Split only the already-rendered final suffix.
                    const auto prompt = apply_chat_template(template_type, messages, {}, true, false);
                    const auto suffix = apply_chat_template(template_type, {messages.back()}, {}, true, false);
                    if (prompt.size()<=suffix.size() || prompt.compare(prompt.size()-suffix.size(),suffix.size(),suffix)!=0)
                        throw std::runtime_error("Cannot split MiniCPM image prompt");
                    ctx = model.prefill(prompt.substr(0,prompt.size()-suffix.size()));
                    ctx = model.prefill(suffix, image, ctx);
                } else {
                    const auto prompt = apply_chat_template(template_type, messages, {}, true, false);
                    ctx = model.prefill(prompt);
                }
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
