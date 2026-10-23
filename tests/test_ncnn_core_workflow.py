"""Static CI coverage guards; these do not compile ncnn or run inference."""
from pathlib import Path
import re
import shlex
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NcnnCoreWorkflowTests(unittest.TestCase):
    def setUp(self):
        workflow = (ROOT / '.github/workflows/ncnn-core.yml').read_text(encoding='utf-8')
        self.cpu = workflow.split('  core:', 1)[1].split('  vulkan-lavapipe:', 1)[0]
        self.cmake = (ROOT / 'model-tests/ncnn-core/CMakeLists.txt').read_text(encoding='utf-8')

    def test_changed_operator_targets_are_built_and_selected(self):
        build = next(line.strip()[5:] for line in self.cpu.splitlines()
                     if line.strip().startswith('run: cmake --build'))
        ctest = next(line.strip()[5:] for line in self.cpu.splitlines()
                     if line.strip().startswith('run: ctest'))
        build_args, test_args = shlex.split(build), shlex.split(ctest)
        pattern = re.compile(test_args[test_args.index('-R') + 1])
        for target in ('test_relu', 'test_shufflechannel', 'test_convolutiondepthwise_5x5_smoke'):
            with self.subTest(target=target):
                self.assertIn(target, build_args[build_args.index('--target') + 1:])
                self.assertIsNotNone(pattern.fullmatch(target))
        self.assertIn('--no-tests=error', test_args)

    def test_depthwise_smoke_is_registered_with_upstream_testutil(self):
        target = 'test_convolutiondepthwise_5x5_smoke'
        self.assertIn(f'add_executable({target} {target}.cpp)', self.cmake)
        self.assertIn(f'target_link_libraries({target} PRIVATE ncnntestutil ncnn)', self.cmake)
        self.assertIn(f'add_test(NAME {target} COMMAND {target})', self.cmake)
        source = (ROOT / f'model-tests/ncnn-core/{target}.cpp').read_text(encoding='utf-8')
        self.assertIn('#include "test_convolutiondepthwise.cpp"', source)
        self.assertIn('test_convolutiondepthwise(w, h, c, c, 5, 1, stride, 0, bias, c, 0)', source)

    def test_bf16_and_architecture_scope_are_explicit(self):
        self.assertIn('set(NCNN_BF16 ON CACHE BOOL "" FORCE)', self.cmake)
        self.assertIn('not execution of the RISC-V kernels', self.cpu)


if __name__ == '__main__':
    unittest.main()
