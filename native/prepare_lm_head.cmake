# Adapt the reviewed upstream file only in the build directory. The bare Gemm
# does not have Net's automatic dtype conversion, so its pipeline and FP32
# extractor output must agree on every CPU, including ARM with FP16 support.
function(agent_prepare_lm_head input output)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${input}")
    # Accept Git CRLF checkouts without accepting unreviewed source changes.
    file(READ "${input}" source)
    string(REPLACE "\r\n" "\n" source "${source}")
    string(SHA256 source_sha256 "${source}")
    if(NOT source_sha256 STREQUAL "b984d0ee23c9aa1a6e81e8fa78ededc277b35f9d5ca16a596adde9c0071a66bf")
        message(FATAL_ERROR "Unreviewed ncnn_llm LM-head source. Revalidate the shared-head precision adaptation before updating its SHA-256.")
    endif()
    set(old_pipeline [=[#if defined(__riscv)
    // On riscv the bare-layer Gemm only built the fp16s pipeline, but the
    // decoder hidden states arrive as fp32 (extract() deinterleaves), which
    // then falls into the never-built fp32 path and crashes (empty BT_data).
    // Build the pipeline as fp32: LM head is an M=1 bandwidth-bound GEMV,
    // so the fp16 savings do not matter here.
    ncnn::Option opt_fp32 = opt;
    opt_fp32.use_fp16_storage = false;
    opt_fp32.use_fp16_arithmetic = false;
    opt_fp32.use_fp16_packed = false;
    int ret_pipe = gemm_layer_->create_pipeline(opt_fp32);
#else
    int ret_pipe = gemm_layer_->create_pipeline(opt);
#endif]=])
    set(new_pipeline [=[    // Local Agent: extractor outputs and model-bin decoded embedding weights
    // are FP32. Bare Gemm bypasses Net's input casts on ARM as well as RISC-V.
    const ncnn::Option shared_opt = shared_lm_head_fp32_option(opt);
    int ret_pipe = gemm_layer_->create_pipeline(shared_opt);]=])
    set(option_helper [=[namespace ncnn_llm {

static ncnn::Option shared_lm_head_fp32_option(const ncnn::Option& opt) {
    ncnn::Option fp32 = opt;
    fp32.use_fp16_storage = false;
    fp32.use_fp16_arithmetic = false;
    fp32.use_fp16_packed = false;
    fp32.use_bf16_storage = false;
    return fp32;
}]=])
    string(REPLACE "${old_pipeline}" "${new_pipeline}" source "${source}")
    string(REPLACE "namespace ncnn_llm {" "${option_helper}" source "${source}")
    string(REPLACE "gemm_layer_->forward(bottom_blobs, top_blobs, opt);"
        "gemm_layer_->forward(bottom_blobs, top_blobs, shared_lm_head_fp32_option(opt));"
        source "${source}")
    # Fail closed if the reviewed transformation was not applied completely.
    string(FIND "${source}" "create_pipeline(shared_opt)" pipeline_pos)
    string(FIND "${source}" "forward(bottom_blobs, top_blobs, shared_lm_head_fp32_option(opt))" forward_pos)
    string(FIND "${source}" "create_pipeline(opt_fp32)" old_pos)
    if(pipeline_pos EQUAL -1 OR forward_pos EQUAL -1 OR NOT old_pos EQUAL -1)
        message(FATAL_ERROR "Shared LM-head precision adaptation did not match the reviewed source")
    endif()
    get_filename_component(output_dir "${output}" DIRECTORY)
    file(MAKE_DIRECTORY "${output_dir}")
    file(WRITE "${output}" "${source}")
endfunction()
