"""Small fixtures test validation logic ONLY; the separate CPU workflow uses real weights."""
import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest
from scripts.qwen05_export import EXPECTED_CONFIG, Graph, SafeWeights, validate_config, sha256
from scripts.qwen05_cpu_test import agent_checks, verify_model
from local_agent.paths import Workspace


class Qwen05Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)

    def test_exact_model_geometry_accepted(self):
        validate_config(copy.deepcopy(EXPECTED_CONFIG))

    def test_06b_cannot_be_substituted(self):
        c = copy.deepcopy(EXPECTED_CONFIG); c['hidden_size'] = 1024
        with self.assertRaises(ValueError): validate_config(c)

    def test_wrong_layer_count_rejected(self):
        c = copy.deepcopy(EXPECTED_CONFIG); c['num_hidden_layers'] = 28
        with self.assertRaises(ValueError): validate_config(c)

    def test_sliding_window_rejected(self):
        c = copy.deepcopy(EXPECTED_CONFIG); c['use_sliding_window'] = True
        with self.assertRaises(ValueError): validate_config(c)

    def test_sha256_actual_bytes(self):
        p = self.path / 'x'; p.write_bytes(b'abc')
        self.assertEqual(sha256(p), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')

    def test_invalid_safetensors_header_rejected(self):
        p = self.path / 'x'; p.write_bytes(struct.pack('<Q', 999999999999))
        with self.assertRaises(ValueError): SafeWeights(p)

    def test_truncated_safetensors_rejected(self):
        p = self.path / 'x'; p.write_bytes(b'abcd')
        with self.assertRaises(ValueError): SafeWeights(p)

    def test_graph_undefined_input_rejected(self):
        with self.assertRaises(ValueError): Graph().add('Swish', 'x', ['undefined'], ['out'])

    def test_graph_duplicate_output_rejected(self):
        g = Graph(); g.add('Input', 'x', [], ['x'])
        with self.assertRaises(ValueError): g.add('Swish', 'y', ['x'], ['x'])

    def test_graph_fanout_inserts_split(self):
        g = Graph(); g.add('Input', 'in', [], ['in0'])
        g.add('Swish', 'a', ['in0'], ['a']); g.add('Swish', 'b', ['in0'], ['b'])
        self.assertIn('Split split_in0 1 2 in0 in0_split_0 in0_split_1', g.text())
        self.assertIn('Swish a 1 1 in0_split_0 a', g.text())
        self.assertEqual(g.text().splitlines()[1], '4 5')

    def test_single_consumer_needs_no_split(self):
        g = Graph(); g.add('Input', 'in', [], ['in0']); g.add('Swish', 'a', ['in0'], ['a'])
        self.assertNotIn('Split', g.text())

    def test_wrong_model_identity_rejected(self):
        (self.path / 'EXPORT.json').write_text(json.dumps({'model_id': 'fake', 'revision': 'a', 'source_weight_sha256': 'b'}))
        with self.assertRaises(ValueError): verify_model(self.path)

    def test_final_without_tools_not_success(self):
        ws = Workspace(self.path / 'ws'); ws.write('proof.txt', 'value')
        checks = agent_checks({'ok': True}, [], ws, 'proof.txt', 'value')
        self.assertFalse(all(checks.values()))

    def test_read_must_verify_the_expected_content(self):
        ws = Workspace(self.path / 'ws'); ws.write('proof.txt', 'value')
        events = [{'event': 'tool_result', 'tool': t, 'arguments': {'path': 'proof.txt'},
                   'result': {'ok': True, 'result': {'content': 'wrong'}}} for t in ['files.write', 'files.read']]
        checks = agent_checks({'ok': True}, events, ws, 'proof.txt', 'value')
        self.assertFalse(checks['model_requested_read'])

    def test_success_requires_real_file_and_both_tool_events(self):
        ws = Workspace(self.path / 'ws'); ws.write('proof.txt', 'value')
        events = [{'event': 'tool_result', 'tool': t, 'arguments': {'path': 'proof.txt'},
                   'result': {'ok': True, 'result': {'content': 'value'}}} for t in ['files.write', 'files.read']]
        self.assertTrue(all(agent_checks({'ok': True}, events, ws, 'proof.txt', 'value').values()))
