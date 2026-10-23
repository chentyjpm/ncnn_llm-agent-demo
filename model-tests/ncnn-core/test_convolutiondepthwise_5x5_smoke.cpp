// Reuse pinned upstream numeric/storage comparisons with a bounded shape set.
// On x86 this tests integration, not the RISC-V four-row/two-column kernels.
// GPU helpers may skip; this is not physical-GPU or model-inference evidence.
#define main ncnn_upstream_convolutiondepthwise_main
#include "test_convolutiondepthwise.cpp"
#undef main

int main()
{
    SRAND(7767517);
    // Packed and unpacked channels; output shapes straddle the four-row and
    // two-column boundaries and retain one-row/one-column remainder paths.
    const int channels[] = {7, 8, 16};
    const int output_shapes[][2] = {{1, 1}, {2, 4}, {3, 7}, {4, 8}};
    for (int ci = 0; ci < 3; ci++)
    {
        const int c = channels[ci];
        for (int stride = 1; stride <= 2; stride++)
        {
            for (int si = 0; si < 4; si++)
            {
                const int w = (output_shapes[si][0] - 1) * stride + 5;
                const int h = (output_shapes[si][1] - 1) * stride + 5;
                for (int bias = 0; bias < 2; bias++)
                {
                    if (test_convolutiondepthwise(w, h, c, c, 5, 1, stride, 0, bias, c, 0) != 0)
                        return 1;
                }
            }
        }
    }
    fprintf(stderr, "PASS: 48 bounded 5x5 depthwise cases (host architecture only; helper GPU skips remain possible)\n");
    return 0;
}
