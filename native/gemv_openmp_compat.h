#pragma once

#include <platform.h>
#if defined(_OPENMP) && NCNN_OPENMP
#include <omp.h>
#endif

inline int agent_gemv_max_threads()
{
#if defined(_OPENMP) && NCNN_OPENMP
    return omp_get_max_threads();
#else
    return 1;
#endif
}
