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

# Bounded INT8 depthwise tail/packing/requantization coverage; host architecture.
add_executable(test_convolutiondepthwise_int8_smoke test_convolutiondepthwise_int8_smoke.cpp)
target_include_directories(test_convolutiondepthwise_int8_smoke PRIVATE "${AGENT_NCNN_SOURCE_DIR}/tests")
target_link_libraries(test_convolutiondepthwise_int8_smoke PRIVATE ncnntestutil ncnn)
add_test(NAME test_convolutiondepthwise_int8_smoke COMMAND test_convolutiondepthwise_int8_smoke)
set_tests_properties(test_convolutiondepthwise_int8_smoke PROPERTIES TIMEOUT 300)

# Finite independent expectations for generic and architecture-selected CPU
# LogSumExp, rather than accepting agreement between two overflowing kernels.
add_executable(test_reduction_logsumexp test_reduction_logsumexp.cpp)
target_include_directories(test_reduction_logsumexp PRIVATE "${AGENT_NCNN_SOURCE_DIR}/src/layer")
target_link_libraries(test_reduction_logsumexp PRIVATE ncnn)
add_test(NAME test_reduction_logsumexp COMMAND test_reduction_logsumexp)
set_tests_properties(test_reduction_logsumexp PROPERTIES TIMEOUT 120)

# Isolate allocator negative controls from unrelated upstream Net cases while
# retaining the complete test_net positive suite. Storage checks require all
# relevant builtins and BF16, rather than silently skipping missing layers.
foreach(net_test test_net_custom_allocator test_net_storage_featmask)
    add_executable(${net_test} ${net_test}.cpp)
    target_include_directories(${net_test} PRIVATE "${AGENT_NCNN_SOURCE_DIR}/tests")
    target_link_libraries(${net_test} PRIVATE ncnntestutil ncnn)
    add_test(NAME ${net_test} COMMAND ${net_test})
    set_tests_properties(${net_test} PROPERTIES TIMEOUT 300)
endforeach()

# Exact-reference negative padding targets the changed x86 SSE2 pack8 code.
if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64|amd64|i.86)$")
    add_executable(test_padding_int8_negative test_padding_int8_negative.cpp)
    target_include_directories(test_padding_int8_negative PRIVATE "${AGENT_NCNN_SOURCE_DIR}/src/layer")
    target_link_libraries(test_padding_int8_negative PRIVATE ncnn)
    add_test(NAME test_padding_int8_negative COMMAND test_padding_int8_negative)
    set_tests_properties(test_padding_int8_negative PROPERTIES TIMEOUT 120)
endif()

# Validate independent arithmetic expectations even without a Vulkan device.
# This is an oracle self-check, not GPU operator execution.
add_executable(test_reduction_fp32_accum_reference test_reduction_fp32_accum_reference.cpp)
target_link_libraries(test_reduction_fp32_accum_reference PRIVATE ncnn)
add_test(NAME test_reduction_fp32_accum_reference COMMAND test_reduction_fp32_accum_reference)
set_tests_properties(test_reduction_fp32_accum_reference PROPERTIES TIMEOUT 120)

if(AGENT_NCNN_VULKAN_TESTS)
    # Upstream helpers permit GPU skips. The strict companions instead require
    # a device, valid pipelines and submitted compute/copy work.
    foreach(strict_test test_vulkan_required test_vulkan_matrix_required test_vulkan_int8_storage test_vulkan_interp_coordinates test_vulkan_priorbox test_vulkan_stability)
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

# No skip/fallback: execute the changed ARMv8.2 FP16 pack8 kernel directly.
option(AGENT_NCNN_ARM_FP16_TESTS "Require direct ARM FP16 depthwise regression" OFF)
if(AGENT_NCNN_ARM_FP16_TESTS)
    add_executable(test_arm_fp16_depthwise test_arm_fp16_depthwise.cpp)
    target_include_directories(test_arm_fp16_depthwise PRIVATE "${AGENT_NCNN_SOURCE_DIR}/src/layer/arm")
    target_link_libraries(test_arm_fp16_depthwise PRIVATE ncnn)
    add_test(NAME test_arm_fp16_depthwise COMMAND test_arm_fp16_depthwise)
    set_tests_properties(test_arm_fp16_depthwise PROPERTIES TIMEOUT 120)
endif()

# The BF16 storage kernel only needs baseline AArch64 NEON, not BF16 ISA.
if(AGENT_NCNN_ARM_FP16_TESTS)
    add_executable(test_arm_bf16_pooling test_arm_bf16_pooling.cpp)
    target_include_directories(test_arm_bf16_pooling PRIVATE "${AGENT_NCNN_SOURCE_DIR}/src/layer/arm")
    target_link_libraries(test_arm_bf16_pooling PRIVATE ncnn)
    add_test(NAME test_arm_bf16_pooling COMMAND test_arm_bf16_pooling)
    set_tests_properties(test_arm_bf16_pooling PROPERTIES TIMEOUT 120)
endif()
