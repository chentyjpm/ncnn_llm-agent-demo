cmake_minimum_required(VERSION 3.20)
project(agent_ncnn_core_regression LANGUAGES C CXX)
# Build the pinned upstream tests unchanged. This is operator/runtime coverage,
# not trained-model inference. The default CPU configuration keeps higher x86
# ISAs out so the changed SSE2 INT8 Winograd TILE_K path is actually exercised.
if(NOT IS_DIRECTORY "${AGENT_NCNN_SOURCE_DIR}")
    message(FATAL_ERROR "AGENT_NCNN_SOURCE_DIR must name the verified ncnn checkout")
endif()
set(NCNN_BUILD_TESTS ON CACHE BOOL "" FORCE)
set(NCNN_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(NCNN_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(NCNN_BUILD_BENCHMARK OFF CACHE BOOL "" FORCE)
option(AGENT_NCNN_VULKAN_TESTS "Require direct Vulkan operator regression" OFF)
set(NCNN_VULKAN ${AGENT_NCNN_VULKAN_TESTS} CACHE BOOL "" FORCE)
set(NCNN_INT8 ON CACHE BOOL "" FORCE)
set(NCNN_BF16 ON CACHE BOOL "" FORCE)
set(NCNN_RUNTIME_CPU OFF CACHE BOOL "" FORCE)
set(NCNN_AVX OFF CACHE BOOL "" FORCE)
set(NCNN_AVX2 OFF CACHE BOOL "" FORCE)
set(NCNN_AVX512 OFF CACHE BOOL "" FORCE)
set(NCNN_SSE2 ON CACHE BOOL "" FORCE)
enable_testing()
add_subdirectory("${AGENT_NCNN_SOURCE_DIR}" ncnn)
if(NCNN_OPENMP AND NCNN_SIMPLEOMP)
    # ncnn's simpleomp compile flag is PRIVATE. Without this flag the test's
    # nested pragmas are ignored and its state-restoration assertions are vacuous.
    target_compile_options(test_cpu PRIVATE -fopenmp)
endif()

if(AGENT_NCNN_VULKAN_TESTS)
    # Upstream's layer helpers permit GPU skips. This additional test instead
    # requires a device, valid pipelines and submitted VkCompute dispatches.
    add_executable(test_vulkan_required test_vulkan_required.cpp)
    target_include_directories(test_vulkan_required PRIVATE
        "${AGENT_NCNN_SOURCE_DIR}/tests"
        "${AGENT_NCNN_SOURCE_DIR}/src/layer"
        "${AGENT_NCNN_SOURCE_DIR}/src/layer/vulkan")
    target_link_libraries(test_vulkan_required PRIVATE ncnntestutil ncnn)
    add_test(NAME test_vulkan_required COMMAND test_vulkan_required)
    set_tests_properties(test_vulkan_required PROPERTIES TIMEOUT 300)
endif()
