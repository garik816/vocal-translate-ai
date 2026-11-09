"""Synthetic tests for the v8 vocal-first workflow; no audio or GPU needed."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from vta import ace, guide


class GuideAlignmentTests(unittest.TestCase):
    def test_duration_ratio_is_pitch_preserving_atempo_factor(self):
        self.assertAlmostEqual(guide.timing_ratio(181.0, 180.0), 181.0 / 180.0)

    def test_wrong_song_duration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "differs too much"):
            guide.timing_ratio(230.0, 180.0)

    def test_negative_duration_is_rejected(self):
        with self.assertRaises(ValueError):
            guide.timing_ratio(-1.0, 180.0)

    def test_external_guide_is_selected_by_song_name(self):
        with tempfile.TemporaryDirectory() as root:
            in_dir = Path(root)
            (in_dir / "guide").mkdir()
            vocal = in_dir / "guide" / "song.wav"
            vocal.write_bytes(b"test")
            with patch.object(guide, "INPUT_DIR", in_dir):
                self.assertEqual(
                    guide.find_external_guide(in_dir / "song.mp3", 2),
                    vocal,
                )


class VocalFirstTests(unittest.TestCase):
    def test_external_guide_never_runs_asr_pitch_or_diffsinger(self):
        with tempfile.TemporaryDirectory() as root:
            song = Path(root) / "song.mp3"
            guide_file = Path(root) / "sung.wav"
            output = Path(root) / "ready.wav"
            with patch.object(app, "WORK_DIR", Path(root)), \
                 patch.object(app.audio, "run_demucs", return_value={"vocals": song}) as demucs, \
                 patch.object(app.audio, "build_instrumental", return_value=song), \
                 patch.object(app.audio, "build_reference", return_value=song), \
                 patch.object(app.guide, "find_external_guide", return_value=guide_file), \
                 patch.object(app.guide, "prepare_guide", return_value=output), \
                 patch.object(app.audio, "render_guide_preview", return_value=output), \
                 patch.object(app.asr, "extract_source_lyrics") as asr, \
                 patch.object(app.melody, "extract_melody") as melody, \
                 patch.object(app.diffsinger, "render_ukrainian_guide") as synth:
                result = app.process_track(
                    song, {"vocal_generation_mode": "external_guide"},
                    "ffmpeg", 1, extract_only=False, guide_only=True,
                )
                self.assertEqual(result, [output])
                demucs.assert_called_once()
                asr.assert_not_called()
                melody.assert_not_called()
                synth.assert_not_called()

    def test_ace_only_does_not_load_demucs_or_seedvc(self):
        with tempfile.TemporaryDirectory() as root:
            song = Path(root) / "song.mp3"
            song.write_bytes(b"test")
            generated = Path(root) / "cover.wav"
            with patch.object(app, "WORK_DIR", Path(root)), \
                 patch.object(app, "find_lyrics_for", return_value=Path(root) / "lyrics.txt"), \
                 patch.object(app.net, "health_ok", return_value=True), \
                 patch.object(app.ace, "generate", return_value=[generated]) as cover, \
                 patch.object(app.audio, "run_demucs") as demucs, \
                 patch.object(app.seed, "convert") as seed:
                result = app.process_track(
                    song, {"ace_source_mode": "full_mix"},
                    "ffmpeg", 1, extract_only=False, guide_only=False,
                    ace_only=True,
                )
                self.assertEqual(result, [generated])
                cover.assert_called_once()
                demucs.assert_not_called()
                seed.assert_not_called()

    def test_old_ace_header_not_passed_to_new_api(self):
        with tempfile.TemporaryDirectory() as root:
            text = Path(root) / "lyrics.txt"
            text.write_text("[Verse]\nУкраїнський текст", encoding="utf-8")
            result = ace.lyrics_text(text, "uk")
            self.assertTrue(result.startswith("[Verse]"))
            self.assertNotIn("# Languages", result)


if __name__ == "__main__":
    unittest.main()
