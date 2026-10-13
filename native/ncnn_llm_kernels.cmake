# The pinned ncnn_llm kernel layout is not covered by the original src/*.cpp glob.
# Keep upstream checkouts read-only and isolate ISA-specific code from the bridge.
include(CheckCXXSourceCompiles)
option(AGENT_LLM_AVX2 "Build runtime-dispatched AVX2/FMA LM-head kernels when supported" ON)
set(AGENT_NCNN_INTERNAL_SOURCE_DIR "" CACHE PATH
    "Installed-SDK mode: matching pinned ncnn source root containing internal layer headers")

function(agent_add_ncnn_llm_kernels target)
    file(GLOB kernel_sources CONFIGURE_DEPENDS "${NCNN_LLM_SOURCE_DIR}/src/kernel/*.cpp")
    if(NOT kernel_sources)
        message(FATAL_ERROR "Pinned ncnn_llm kernel sources are missing; validate the dependency lock")
    endif()
    include("${CMAKE_CURRENT_FUNCTION_LIST_DIR}/prepare_lm_head.cmake")
    set(original_head "${NCNN_LLM_SOURCE_DIR}/src/kernel/lm_head.cpp")
    set(adapted_head "${CMAKE_CURRENT_BINARY_DIR}/generated/lm_head.cpp")
    agent_prepare_lm_head("${original_head}" "${adapted_head}")
    list(REMOVE_ITEM kernel_sources "${original_head}")
    target_sources(${target} PRIVATE ${kernel_sources} "${adapted_head}")

    # Check compiler target macros, not the build host or WIN32 (Windows ARM64 exists).
    # ncnn itself does not support a mixed-architecture SDK in one build directory.
    if(APPLE AND CMAKE_OSX_ARCHITECTURES)
        list(LENGTH CMAKE_OSX_ARCHITECTURES arch_count)
        if(arch_count GREATER 1)
            message(FATAL_ERROR "Build native dependencies separately for each CMAKE_OSX_ARCHITECTURES value")
        endif()
    endif()
    set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)
    check_cxx_source_compiles("
#if !defined(__i386__) && !defined(__x86_64__) && !defined(_M_IX86) && !defined(_M_X64)
#error Not an x86 target
#endif
int main() { return 0; }" AGENT_TARGET_X86)

    # Always compile the portable implementation: it also makes the tests useful
    # on ARM, where the upstream caller uses ncnn's normal Gemm path instead.
    add_library(agent_ncnn_gemv STATIC "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/gemv_dispatch.cpp")
    target_include_directories(agent_ncnn_gemv PUBLIC
        "${CMAKE_CURRENT_FUNCTION_LIST_DIR}" "${NCNN_LLM_SOURCE_DIR}/src")
    target_link_libraries(agent_ncnn_gemv PUBLIC ${NCNN_TARGET})
    target_link_libraries(${target} PUBLIC agent_ncnn_gemv)
    if(MSVC)
        target_compile_options(agent_ncnn_gemv PRIVATE /utf-8 /EHsc)
    endif()

    if(AGENT_NCNN_SOURCE_DIR)
        set(internal_source "${AGENT_NCNN_SOURCE_DIR}")
        if(AGENT_NCNN_INTERNAL_SOURCE_DIR)
            file(REAL_PATH "${AGENT_NCNN_SOURCE_DIR}" built_source)
            file(REAL_PATH "${AGENT_NCNN_INTERNAL_SOURCE_DIR}" requested_source)
            if(NOT built_source STREQUAL requested_source)
                message(FATAL_ERROR "Source builds must use internal ncnn headers from AGENT_NCNN_SOURCE_DIR")
            endif()
        endif()
    else()
        set(internal_source "${AGENT_NCNN_INTERNAL_SOURCE_DIR}")
        if(NOT internal_source)
            message(FATAL_ERROR
                "The pinned ncnn_llm requires ncnn internal headers omitted from the SDK. "
                "Set AGENT_NCNN_INTERNAL_SOURCE_DIR to the matching pinned ncnn source root, "
                "or use AGENT_NCNN_SOURCE_DIR for a source build. No headers are downloaded or guessed.")
        endif()
        get_target_property(sdk_includes ${NCNN_TARGET} INTERFACE_INCLUDE_DIRECTORIES)
        set(sdk_header_dir "")
        foreach(include_dir IN LISTS sdk_includes)
            if(EXISTS "${include_dir}/cpu.h" AND EXISTS "${include_dir}/mat.h")
                set(sdk_header_dir "${include_dir}")
                break()
            endif()
        endforeach()
        if(NOT sdk_header_dir)
            message(FATAL_ERROR "Cannot verify the installed ncnn SDK public headers; use AGENT_NCNN_SOURCE_DIR")
        endif()
        # Do not silently mix public and internal headers from different releases.
        # The caller must still supply the source revision used to build their SDK.
        foreach(header cpu.h mat.h layer.h option.h paramdict.h modelbin.h)
            if(NOT EXISTS "${internal_source}/src/${header}" OR NOT EXISTS "${sdk_header_dir}/${header}")
                message(FATAL_ERROR "Missing matching ncnn SDK/source header: ${header}")
            endif()
            file(READ "${internal_source}/src/${header}" source_header)
            file(READ "${sdk_header_dir}/${header}" sdk_header)
            string(REPLACE "\r\n" "\n" source_header "${source_header}")
            string(REPLACE "\r\n" "\n" sdk_header "${sdk_header}")
            string(SHA256 source_hash "${source_header}")
            string(SHA256 sdk_hash "${sdk_header}")
            if(NOT source_hash STREQUAL sdk_hash)
                message(FATAL_ERROR "ncnn SDK/source header mismatch: ${header}; use the SDK's matching pinned source")
            endif()
        endforeach()
    endif()
    if(NOT EXISTS "${internal_source}/src/layer/embed.h")
        message(FATAL_ERROR "Missing ncnn internal layer/embed.h; supply matching pinned ncnn sources")
    endif()
    target_include_directories(${target} PRIVATE "${internal_source}/src/layer")
    if(NOT AGENT_TARGET_X86)
        message(STATUS "ncnn_llm: portable kernels; x86 sources excluded for this compiler target")
        return()
    endif()
    foreach(header layer/x86/x86_usability.h layer/x86/x86_activation.h layer/fused_activation.h)
        if(NOT EXISTS "${internal_source}/src/${header}")
            message(FATAL_ERROR "Missing ncnn internal header ${header}; supply matching pinned ncnn sources")
        endif()
    endforeach()
    target_include_directories(${target} PRIVATE "${internal_source}/src/layer/x86")
    target_sources(${target} PRIVATE
        "${NCNN_LLM_SOURCE_DIR}/src/kernel/x86/gdr_x86.cpp"
        "${NCNN_LLM_SOURCE_DIR}/src/kernel/x86/shortconv_x86.cpp")

    if(NOT AGENT_LLM_AVX2)
        message(STATUS "ncnn_llm: scalar LM-head selected (AGENT_LLM_AVX2=OFF)")
        return()
    endif()
    if(MSVC)
        set(avx2_options /arch:AVX2)
    elseif(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang|IntelLLVM")
        set(avx2_options -mavx2 -mfma)
    else()
        message(STATUS "ncnn_llm: scalar LM-head selected; no AVX2 flags known for this compiler")
        return()
    endif()
    string(JOIN " " CMAKE_REQUIRED_FLAGS ${avx2_options})
    check_cxx_source_compiles("#include <immintrin.h>
int main() {
    __m256 a = _mm256_set1_ps(2.f);
    __m256i b = _mm256_cvtepu16_epi32(_mm_setzero_si128());
    return _mm256_movemask_ps(_mm256_fmadd_ps(a, a, _mm256_castsi256_ps(b)));
}" AGENT_COMPILER_AVX2_FMA)
    if(NOT AGENT_COMPILER_AVX2_FMA)
        message(STATUS "ncnn_llm: scalar LM-head selected; compiler lacks AVX2/FMA intrinsics")
        return()
    endif()

    set(upstream "${NCNN_LLM_SOURCE_DIR}/src/kernel/x86/lm_head_x86.cpp")
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${upstream}")
    # Git may check out CRLF on Windows; all other content changes still fail closed.
    file(READ "${upstream}" source)
    string(REPLACE "\r\n" "\n" source "${source}")
    string(SHA256 upstream_hash "${source}")
    if(NOT upstream_hash STREQUAL "af9102b490943d9b0905fcc72ddb1d6ad845303101bd95d13193829c3c4c6ad5")
        message(FATAL_ERROR "Unreviewed ncnn_llm AVX2 LM-head source; revalidate its adapter before updating the SHA-256")
    endif()
    # Fail closed if the pinned adaptation no longer matches upstream.
    string(REGEX MATCHALL "#include <omp.h>" omp_includes "${source}")
    list(LENGTH omp_includes omp_include_count)
    string(REGEX MATCHALL "omp_get_max_threads\\(\\)" omp_calls "${source}")
    list(LENGTH omp_calls omp_call_count)
    if(NOT omp_include_count EQUAL 1 OR NOT omp_call_count EQUAL 3)
        message(FATAL_ERROR "ncnn_llm LM-head OpenMP adapter no longer matches the pinned source")
    endif()
    string(REPLACE "#include <omp.h>" "#include \"gemv_openmp_compat.h\"" source "${source}")
    string(REPLACE "omp_get_max_threads()" "agent_gemv_max_threads()" source "${source}")
    string(REPLACE "#pragma omp parallel for num_threads(num_threads) schedule(static)"
        "#if defined(_OPENMP) && NCNN_OPENMP\n    #pragma omp parallel for num_threads(num_threads) schedule(static)\n    #endif"
        source "${source}")
    foreach(symbol gemv_fp32_x86 gemv_bf16_x86 gemv_bf16_fp32_x86)
        if(NOT source MATCHES "void ${symbol}\\(")
            message(FATAL_ERROR "ncnn_llm LM-head symbol ${symbol} changed; revalidate the adapter")
        endif()
        string(REPLACE "${symbol}" "${symbol}_avx2" source "${source}")
    endforeach()
    file(MAKE_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/generated")
    set(adapted "${CMAKE_CURRENT_BINARY_DIR}/generated/lm_head_x86_avx2.cpp")
    file(WRITE "${adapted}" "// Generated from the pinned ncnn_llm source; do not edit.\n${source}")
    add_library(agent_ncnn_gemv_avx2 OBJECT "${adapted}")
    target_include_directories(agent_ncnn_gemv_avx2 PRIVATE
        "${CMAKE_CURRENT_FUNCTION_LIST_DIR}" "${NCNN_LLM_SOURCE_DIR}/src")
    target_link_libraries(agent_ncnn_gemv_avx2 PRIVATE ${NCNN_TARGET})
    target_compile_options(agent_ncnn_gemv_avx2 PRIVATE ${avx2_options})
    # Keep the ISA-specific translation unit isolated even in an IPO build.
    set_property(TARGET agent_ncnn_gemv_avx2 PROPERTY INTERPROCEDURAL_OPTIMIZATION FALSE)
    if(MSVC)
        target_compile_options(agent_ncnn_gemv_avx2 PRIVATE /utf-8 /EHsc)
    endif()
    target_sources(agent_ncnn_gemv PRIVATE $<TARGET_OBJECTS:agent_ncnn_gemv_avx2>)
    target_compile_definitions(agent_ncnn_gemv PRIVATE AGENT_GEMV_HAS_AVX2=1)
    message(STATUS "ncnn_llm: isolated AVX2/FMA LM-head with baseline-safe runtime dispatch")
endfunction()
