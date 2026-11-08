// Keep this negative control independent of earlier upstream test_net cases.
// The original upstream test_net target must still run in the positive suite.
#define main upstream_test_net_main
#include "test_net.cpp"
#undef main

int main()
{
    const int ret = test_extract_input_with_custom_blob_allocator();
    if (ret != 0)
    {
        fprintf(stderr, "CUSTOM_ALLOCATOR_REGRESSION: upstream custom allocator case failed\n");
        return 2;
    }
    fprintf(stderr, "PASS custom allocator: unchanged upstream isolated case\n");
    return 0;
}
