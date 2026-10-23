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

add_executable(test_convolution_int8_smoke test_convolution_int8_smoke.cpp)
target_include_directories(test_convolution_int8_smoke PRIVATE "${AGENT_NCNN_SOURCE_DIR}/tests")
target_link_libraries(test_convolution_int8_smoke PRIVATE ncnntestutil ncnn)
add_test(NAME test_convolution_int8_smoke COMMAND test_convolution_int8_smoke)
set_tests_properties(test_convolution_int8_smoke PROPERTIES TIMEOUT 600)

# Bound the 5x5 stride-1/2 shape matrix while retaining upstream comparisons
# across the storage/packing options supported by the executing architecture.
add_executable(test_convolutiondepthwise_5x5_smoke test_convolutiondepthwise_5x5_smoke.cpp)
target_include_directories(test_convolutiondepthwise_5x5_smoke PRIVATE "${AGENT_NCNN_SOURCE_DIR}/tests")
target_link_libraries(test_convolutiondepthwise_5x5_smoke PRIVATE ncnntestutil ncnn)
add_test(NAME test_convolutiondepthwise_5x5_smoke COMMAND test_convolutiondepthwise_5x5_smoke)
set_tests_properties(test_convolutiondepthwise_5x5_smoke PROPERTIES TIMEOUT 300)

if(AGENT_NCNN_VULKAN_TESTS)
    # Upstream helpers permit GPU skips. The strict companions instead require
    # a device, valid pipelines and submitted compute/copy work.
    foreach(strict_test test_vulkan_required test_vulkan_matrix_required)
        add_executable(${strict_test} ${strict_test}.cpp)
        target_include_directories(${strict_test} PRIVATE
            "${AGENT_NCNN_SOURCE_DIR}/tests"
            "${AGENT_NCNN_SOURCE_DIR}/src/layer"
            "${AGENT_NCNN_SOURCE_DIR}/src/layer/vulkan")
        target_link_libraries(${strict_test} PRIVATE ncnntestutil ncnn)
        add_test(NAME ${strict_test} COMMAND ${strict_test})
        set_tests_properties(${strict_test} PROPERTIES TIMEOUT 600)
    endforeach()
endif()
