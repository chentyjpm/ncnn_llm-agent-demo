// Exact, independent scalar expectations for all four SSE2 pack8 constant
// Padding branches. No test helper, CPU fallback, tolerance or model weights.
// This test has no intrinsics. ncnn compiles its SSE2 implementation privately,
// so select the supported architecture without depending on its private macro.
#include "mat.h"
#include "paramdict.h"
#include <stdio.h>
#if defined(__x86_64__) || defined(_M_X64) || defined(__i386__) || defined(_M_IX86)
#include "x86/padding_x86.h"

struct Shape
{
    int w, h, d, c;
};

static Shape scalar_shape(const ncnn::Mat& m)
{
    Shape s = {m.w, m.h, m.d, m.c};
    if (m.dims == 1) s.w *= m.elempack;
    else if (m.dims == 2) s.h *= m.elempack;
    else s.c *= m.elempack;
    return s;
}

static signed char& at(ncnn::Mat& m, int x, int y, int z, int c)
{
    if (m.dims == 1) return ((signed char*)m.data)[x];
    if (m.dims == 2) return m.row<signed char>(y / 8)[x * 8 + y % 8];
    if (m.dims == 3) return m.channel(c / 8).row<signed char>(y)[x * 8 + c % 8];
    return m.channel(c / 8).depth(z).row<signed char>(y)[x * 8 + c % 8];
}

static signed char input_value(int x, int y, int z, int c)
{
    return (signed char)(-117 + (x * 7 + y * 13 + z * 17 + c * 23) % 235);
}

static int run_case(int dims, int width, int value)
{
    ncnn::Mat input;
    if (dims == 1) input.create(width, (size_t)8u, 8);
    if (dims == 2) input.create(width, 2, (size_t)8u, 8);
    if (dims == 3) input.create(width, 3, 2, (size_t)8u, 8);
    if (dims == 4) input.create(width, 3, 2, 2, (size_t)8u, 8);
    if (input.empty()) return 1;
    const Shape s = scalar_shape(input);
    for (int c = 0; c < s.c; c++)
        for (int z = 0; z < s.d; z++)
            for (int y = 0; y < s.h; y++)
                for (int x = 0; x < s.w; x++)
                    at(input, x, y, z, c) = input_value(x, y, z, c);

    const int left = dims == 1 ? 8 : 2;
    const int right = dims == 1 ? 16 : 3;
    const int top = dims == 1 ? 0 : dims == 2 ? 8 : 1;
    const int bottom = dims == 1 ? 0 : dims == 2 ? 16 : 2;
    const int front = dims == 3 ? 8 : dims == 4 ? 1 : 0;
    const int behind = dims == 3 ? 16 : dims == 4 ? 2 : 0;
    ncnn::ParamDict pd;
    pd.set(0, top); pd.set(1, bottom); pd.set(2, left); pd.set(3, right);
    pd.set(4, 0); pd.set(5, (float)value); pd.set(7, front); pd.set(8, behind);
    ncnn::Option opt;
    opt.num_threads = 1;
    opt.use_packing_layout = true;
    opt.use_fp16_storage = false;
    opt.use_bf16_storage = false;
    ncnn::Padding_x86 layer;
    ncnn::Mat output;
    if (!layer.support_packing || layer.load_param(pd) || layer.create_pipeline(opt)) return 1;
    const int ret = layer.forward(input, output, opt);
    layer.destroy_pipeline(opt);
    if (ret || output.empty() || output.dims != dims || output.elempack != 8 || output.elembits() != 8)
    {
        fprintf(stderr, "EXECUTION_FAILURE dims=%d width=%d value=%d ret=%d pack=%d bits=%d\n", dims, width, value, ret, output.elempack, output.elembits());
        return 1;
    }
    const Shape o = scalar_shape(output);
    if (o.w != s.w + left + right || o.h != s.h + top + bottom ||
        o.d != s.d + (dims == 4 ? front + behind : 0) ||
        o.c != s.c + (dims == 3 ? front + behind : 0))
    {
        fprintf(stderr, "SHAPE_FAILURE dims=%d width=%d value=%d\n", dims, width, value);
        return 1;
    }
    for (int c = 0; c < o.c; c++)
        for (int z = 0; z < o.d; z++)
            for (int y = 0; y < o.h; y++)
                for (int x = 0; x < o.w; x++)
                {
                    const int ix = x - left, iy = y - top;
                    const int iz = z - (dims == 4 ? front : 0);
                    const int ic = c - (dims == 3 ? front : 0);
                    const bool inside = ix >= 0 && ix < s.w && iy >= 0 && iy < s.h && iz >= 0 && iz < s.d && ic >= 0 && ic < s.c;
                    const int expected = inside ? input_value(ix, iy, iz, ic) : value;
                    const int actual = at(output, x, y, z, c);
                    if (actual != expected)
                    {
                        fprintf(stderr, "NUMERICAL_MISMATCH dims=%d width=%d value=%d x=%d y=%d z=%d c=%d actual=%d expected=%d\n", dims, width, value, x, y, z, c, actual, expected);
                        return 2;
                    }
                }
    return 0;
}
#endif

int main()
{
#if !defined(__x86_64__) && !defined(_M_X64) && !defined(__i386__) && !defined(_M_IX86)
    fprintf(stderr, "CONFIGURATION_FAILURE: this strict companion requires x86 SSE2\n");
    return 1;
#else
    const int values[] = {-128, -23, -2, -1, 0, 127};
    const int widths[] = {1, 5, 17};
    int passed = 0, mismatched = 0, failed = 0;
    for (int dims = 1; dims <= 4; dims++)
        for (size_t w = 0; w < sizeof(widths) / sizeof(widths[0]); w++)
            for (size_t v = 0; v < sizeof(values) / sizeof(values[0]); v++)
            {
                const int ret = run_case(dims, widths[w], values[v]);
                if (ret == 0) passed++;
                else if (ret == 2) mismatched++;
                else failed++;
            }
    fprintf(stderr, "SSE2 pack8 INT8 Padding: passed=%d numerical_mismatches=%d execution_failures=%d total=72\n", passed, mismatched, failed);
    return failed ? 1 : mismatched ? 2 : 0;
#endif
}
