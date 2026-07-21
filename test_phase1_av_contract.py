"""Phase 1: product A/V duration contract (holds + mux argv)."""
from __future__ import annotations

import unittest
from pathlib import Path

from app.product.video import (
    AV_DURATION_TOLERANCE_SEC,
    build_av_mux_command,
    compute_frame_holds,
)


class TestComputeFrameHolds(unittest.TestCase):
    def test_holds_cover_speech_segments(self):
        cues = [
            {"start": 0.0, "end": 6.5, "text": "a"},
            {"start": 6.9, "end": 9.9, "text": "b"},
            {"start": 10.3, "end": 14.8, "text": "c"},
        ]
        audio = 14.8
        holds = compute_frame_holds(3, cues, audio)
        self.assertEqual(len(holds), 3)
        self.assertGreaterEqual(sum(holds) + 1e-9, audio - AV_DURATION_TOLERANCE_SEC)
        # First hold spans to next cue start
        self.assertAlmostEqual(holds[0], 6.9, places=3)
        self.assertAlmostEqual(holds[1], 10.3 - 6.9, places=3)
        # Last hold reaches audio end
        self.assertAlmostEqual(holds[2], audio - 10.3, places=3)

    def test_pads_last_frame_when_cues_shorter_than_audio(self):
        cues = [
            {"start": 0.0, "end": 2.0, "text": "a"},
            {"start": 2.0, "end": 4.0, "text": "b"},
        ]
        audio = 10.0
        holds = compute_frame_holds(2, cues, audio)
        self.assertGreaterEqual(sum(holds), audio - AV_DURATION_TOLERANCE_SEC)
        self.assertAlmostEqual(holds[-1], 8.0, places=3)

    def test_empty_cues_equal_default(self):
        holds = compute_frame_holds(3, [], 0.0, default_hold=2.0)
        self.assertEqual(holds, [2.0, 2.0, 2.0])

    def test_zero_frames(self):
        self.assertEqual(compute_frame_holds(0, [], 5.0), [])


class TestBuildAvMuxCommand(unittest.TestCase):
    def test_aligned_no_shortest(self):
        cmd = build_av_mux_command(
            Path("s.mp4"), Path("a.wav"), Path("o.mp4"),
            video_duration_sec=10.0,
            audio_duration_sec=10.02,
        )
        self.assertNotIn("-shortest", cmd)
        self.assertIn("-c:v", cmd)
        self.assertIn("copy", cmd)

    def test_video_longer_pads_audio_no_shortest(self):
        cmd = build_av_mux_command(
            Path("s.mp4"), Path("a.wav"), Path("o.mp4"),
            video_duration_sec=20.0,
            audio_duration_sec=10.0,
        )
        joined = " ".join(cmd)
        self.assertNotIn("-shortest", cmd)
        self.assertIn("apad=", joined)
        self.assertIn("-t", cmd)

    def test_audio_longer_tpads_video_no_shortest(self):
        cmd = build_av_mux_command(
            Path("s.mp4"), Path("a.wav"), Path("o.mp4"),
            video_duration_sec=5.0,
            audio_duration_sec=12.0,
        )
        joined = " ".join(cmd)
        self.assertNotIn("-shortest", cmd)
        self.assertIn("tpad=", joined)


if __name__ == "__main__":
    unittest.main()
