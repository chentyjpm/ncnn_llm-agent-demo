// Reuse the pinned upstream test function unchanged, with a small selection
// instead of its exhaustive main. GPU helpers may skip: this is supplemental
// coverage, never evidence that an Adreno cooperative-matrix shader ran.
#define main ncnn_upstream_convolution_3a_main
#include "test_convolution_3a.cpp"
#undef main

int main()
{
    SRAND(7767517);
#if NCNN_INT8
    const int ret = 0
        || test_convolution_int8(7, 5, 8, 8, 1, 1, 1, 0, 1)
        || test_convolution_int8(7, 5, 7, 9, 1, 1, 1, 0, 1, true)
        || test_convolution_int8(9, 7, 8, 8, 3, 1, 1, 1, 1, false, 1, false, false, true)
        || test_convolution_int8(9, 7, 7, 9, 3, 1, 1, 1, 1, true, 101, false, false, true)
        || test_convolution_int8(9, 7, 8, 9, 2, 1, 1, 0, 1, false, 1, true);
    if (!ret) fprintf(stderr, "PASS: five focused upstream INT8 convolution inputs (helper GPU skips remain possible)\n");
    return ret;
#else
    fprintf(stderr, "FAIL: focused convolution smoke requires NCNN_INT8\n");
    return 1;
#endif
}
