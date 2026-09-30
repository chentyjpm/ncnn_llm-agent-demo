"""Presentation-only regression fixtures; do not claim OS/GPU/model execution."""
import unittest
from local_agent.monitor_widgets import metric, memory, percent, ellipsis, chart_segments, COLORS


class PresentationTests(unittest.TestCase):
    def test_unknown_values_are_not_zero(self):
        for value in (None, True, False, -1, float('nan'), float('inf'), '12'):
            self.assertIsNone(metric(value))
            self.assertEqual(memory(value), '不可用')
            self.assertEqual(percent(value), '不可用')

    def test_actual_zero_is_displayed(self):
        self.assertEqual(percent(0), '0.0%')
        self.assertEqual(memory(0), '0.0 MiB')

    def test_memory_units_preserved(self):
        self.assertEqual(memory(1024**3), '1.00 GiB')
        self.assertEqual(memory(512*1024**2), '512.0 MiB')

    def test_no_elision_when_text_fits(self):
        self.assertEqual(ellipsis('模型 · Qwen', len, 30), '模型 · Qwen')

    def test_elision_is_bounded_unicode(self):
        self.assertEqual(ellipsis('模型 · Qwen', len, 5), '模型 ·…')
        self.assertEqual(ellipsis('abcdef', len, 0), '')

    def test_elision_has_no_side_effects(self):
        original='Very long model filename'
        ellipsis(original, len, 4)
        self.assertEqual(original, 'Very long model filename')

    def test_no_fabricated_chart_when_unknown(self):
        self.assertEqual(chart_segments([None, float('nan'), None], 100, 30, 100), [])

    def test_chart_does_not_bridge_missing_samples(self):
        segments=chart_segments([10, None, 20, 40], 89, 100, 100)
        self.assertEqual([len(s) for s in segments], [1,2])
        self.assertEqual(segments[-1][-1], (89,60))

    def test_newest_chart_sample_is_right_aligned(self):
        self.assertEqual(chart_segments([25], 89, 100, 100), [[(89,75)]])

    def test_graph_clamps_display_not_input(self):
        values=[500]
        self.assertEqual(chart_segments(values, 89, 100, 100), [[(89,0)]])
        self.assertEqual(values, [500])

    def test_history_window_is_90_samples(self):
        segments=chart_segments([30]*1000, 89, 100, 100)
        self.assertEqual(len(segments[0]),90)
        self.assertEqual(segments[0][0],(0,70))

    def test_dynamic_memory_chart_has_headroom(self):
        y=chart_segments([100],89,100)[0][0][1]
        self.assertGreater(y,0)
        self.assertLess(y,100)

    def test_dark_text_and_primary_button_contrast(self):
        def light(hexcolor):
            rgb=[int(hexcolor[i:i+2],16)/255 for i in (1,3,5)]
            rgb=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in rgb]
            return sum(v*w for v,w in zip(rgb,(.2126,.7152,.0722)))
        for foreground,background in [('text','bg'),('text','panel'),('muted','panel'),('dim','panel'),('accent','selected'),('bg','accent')]:
            a,b=sorted((light(COLORS[foreground]),light(COLORS[background])))
            self.assertGreaterEqual((b+.05)/(a+.05),4.5,(foreground,background))


if __name__=='__main__':unittest.main()
