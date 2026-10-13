#pragma once

// Adapter-private testable entry points. The upstream symbols remain unchanged.
namespace agent_gemv {
bool can_use_avx2(bool cpu_avx2, bool cpu_fma);
bool runtime_has_avx2();
void fp32_scalar(const float*, const float*, float*, int, int, int);
void bf16_scalar(const unsigned short*, const unsigned short*, float*, int, int, int);
void bf16_fp32_scalar(const float*, const unsigned short*, float*, int, int, int);
// allow_avx2=false exercises exactly the fallback used on baseline CPUs.
void fp32_dispatch(const float*, const float*, float*, int, int, int, bool allow_avx2);
void bf16_dispatch(const unsigned short*, const unsigned short*, float*, int, int, int, bool allow_avx2);
void bf16_fp32_dispatch(const float*, const unsigned short*, float*, int, int, int, bool allow_avx2);
}
