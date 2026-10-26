// Execute pinned upstream numerical comparisons with bounded INT8 depthwise
// inputs. Host-only coverage is not proof of RISC-V or ARM kernel execution.
#define main ncnn_upstream_depthwise_main
#include "test_convolutiondepthwise_1.cpp"
#undef main

int main()
{
    SRAND(7767517);
#if NCNN_INT8
    const int channels[] = {7, 8, 31, 32, 33, 64};
    int cases = 0;
    for (int ci = 0; ci < 6; ci++)
    {
        const int c = channels[ci];
        for (int stride = 1; stride <= 2; stride++)
        {
            for (int requant = 0; requant < 2; requant++)
            {
                // Odd output tails, packed boundaries, identity/ReLU and both
                // int8 input scale modes. Upstream compares with scalar output.
                if (test_convolutiondepthwise_int8(11, 9, c, c, 3, 1, stride, 0,
                    1, c, requant != 0, requant ? 102 : 2, true, requant) != 0)
                    return 1;
                cases++;
            }
        }
    }
    fprintf(stderr, "PASS: %d bounded INT8 depthwise cases (executing host only; GPU helper skips possible)\n", cases);
    return 0;
#else
    fprintf(stderr, "FAIL: INT8 depthwise smoke requires NCNN_INT8\n");
    return 1;
#endif
}
