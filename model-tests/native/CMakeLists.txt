cmake_minimum_required(VERSION 3.20)
project(qwen05_cpu_validation LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
add_subdirectory("${CMAKE_CURRENT_LIST_DIR}/../../native" bridge)
add_executable(qwen05_logits qwen05_logits.cpp)
target_link_libraries(qwen05_logits PRIVATE agent_ncnn_llm)
# Inspect the same Embed weights shared by the production runtime. These
# internal headers must come from the ncnn checkout matching the linked ABI.
if(AGENT_NCNN_SOURCE_DIR)
    target_include_directories(qwen05_logits PRIVATE "${AGENT_NCNN_SOURCE_DIR}/src/layer")
else()
    target_include_directories(qwen05_logits PRIVATE "${AGENT_NCNN_INTERNAL_SOURCE_DIR}/src/layer")
endif()
