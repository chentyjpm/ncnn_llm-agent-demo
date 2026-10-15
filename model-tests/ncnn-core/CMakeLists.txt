cmake_minimum_required(VERSION 3.20)
project(agent_ncnn_core_regression LANGUAGES C CXX)
# Build the pinned upstream tests unchanged. This is operator/runtime coverage,
# not trained-model inference. Keep Vulkan and higher x86 ISAs out so the
# changed SSE2 INT8 Winograd TILE_K path is actually exercised.
if(NOT IS_DIRECTORY "${AGENT_NCNN_SOURCE_DIR}")
    message(FATAL_ERROR "AGENT_NCNN_SOURCE_DIR must name the verified ncnn checkout")
endif()
set(NCNN_BUILD_TESTS ON CACHE BOOL "" FORCE)
set(NCNN_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(NCNN_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(NCNN_BUILD_BENCHMARK OFF CACHE BOOL "" FORCE)
set(NCNN_VULKAN OFF CACHE BOOL "" FORCE)
set(NCNN_INT8 ON CACHE BOOL "" FORCE)
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
