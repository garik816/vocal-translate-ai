"""Synthetic tests for the v8 vocal-first workflow; no audio or GPU needed."""
import tempfile
import unittest
import json
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

    def test_missing_ace_cover_fails_before_running_demucs(self):
        with tempfile.TemporaryDirectory() as root:
            song = Path(root) / "song.mp3"
            song.write_bytes(b"test")
            with patch.object(app, "WORK_DIR", Path(root)), \
                 patch.object(app.audio, "run_demucs") as demucs, \
                 patch.object(app.audio, "build_reference") as reference:
                with self.assertRaisesRegex(FileNotFoundError, "ACE_ONLY.bat"):
                    app.process_track(
                        song, {"vocal_generation_mode": "external_guide"},
                        "ffmpeg", 1, extract_only=False, guide_only=True,
                        from_ace=True,
                    )
                demucs.assert_not_called()
                reference.assert_not_called()

    def test_missing_manual_guide_explains_ace_workflow(self):
        with tempfile.TemporaryDirectory() as root:
            song = Path(root) / "song.mp3"
            song.write_bytes(b"test")
            with patch.object(app, "WORK_DIR", Path(root)), \
                 patch.object(app.guide, "find_external_guide",
                              side_effect=FileNotFoundError("No guide")) as finder, \
                 patch.object(app.audio, "run_demucs") as demucs:
                with self.assertRaisesRegex(FileNotFoundError, "ACE_ONLY.bat"):
                    app.process_track(
                        song, {"vocal_generation_mode": "external_guide"},
                        "ffmpeg", 1, extract_only=False, guide_only=True,
                    )
                finder.assert_called_once()
                demucs.assert_not_called()

    def test_auto_guide_uses_existing_ace_cover_if_manual_missing(self):
        with tempfile.TemporaryDirectory() as root:
            song = Path(root) / "song.mp3"
            song.write_bytes(b"test")
            cached = Path(root) / "song" / "ace_guides" / "guide_01.wav"
            cached.parent.mkdir(parents=True)
            cached.write_bytes(b"fake-ace-mix")
            vocal = Path(root) / "isolated.wav"
            output = Path(root) / "preview.mp3"
            with patch.object(app, "WORK_DIR", Path(root)), \
                 patch.object(app.guide, "find_external_guide",
                              side_effect=FileNotFoundError("No guide")), \
                 patch.object(app.audio, "run_demucs",
                              return_value={"vocals": song}) as demucs, \
                 patch.object(app.audio, "build_instrumental", return_value=song), \
                 patch.object(app.audio, "build_reference", return_value=song), \
                 patch.object(app.audio, "isolate_guide_vocals",
                              return_value=[vocal]) as isolate, \
                 patch.object(app.guide, "prepare_guide",
                              return_value=vocal), \
                 patch.object(app.audio, "render_guide_preview",
                              return_value=output):
                result = app.process_track(
                    song, {"vocal_generation_mode": "external_guide"},
                    "ffmpeg", 1, extract_only=False, guide_only=True,
                    auto_guide=True,
                )
                self.assertEqual(result, [output])
                demucs.assert_called_once()
                isolate.assert_called_once()
                self.assertEqual(isolate.call_args.args[0], [cached])

    def test_auto_guide_missing_inputs_fails_before_demucs(self):
        with tempfile.TemporaryDirectory() as root:
            song = Path(root) / "song.mp3"
            song.write_bytes(b"test")
            with patch.object(app, "WORK_DIR", Path(root)), \
                 patch.object(app.guide, "find_external_guide",
                              side_effect=FileNotFoundError("No guide")), \
                 patch.object(app.audio, "run_demucs") as demucs:
                with self.assertRaisesRegex(FileNotFoundError, "START_ACE_API.bat"):
                    app.process_track(
                        song, {"vocal_generation_mode": "external_guide"},
                        "ffmpeg", 1, extract_only=False, guide_only=True,
                        auto_guide=True,
                    )
                demucs.assert_not_called()

    def test_ace_request_uses_live_schema_and_does_not_crash(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            source = folder / "song.mp3"
            source.write_bytes(b"fake-mp3")
            lyrics = folder / "lyrics.txt"
            lyrics.write_text("[Verse]\\nНова мова", encoding="utf-8")
            task = {"code": 200, "data": {"task_id": "task1"}}
            result = {"data": [{"status": 1,
                "result": json.dumps([{"file": "/v1/audio?path=output.wav"}])}]}
            def write_download(url, target):
                target.write_bytes(b"fake-output")
            with patch.object(ace, "audio_duration", return_value=180.17), \
                 patch.object(ace, "post_multipart", return_value=task) as send, \
                 patch.object(ace, "post_json", return_value=result), \
                 patch.object(ace, "download", side_effect=write_download):
                guides = ace.generate(
                    {"reuse_ace_guides": False, "ace_seed": 42, "cover_strength": 0.8},
                    "http://127.0.0.1:8001", source, source, lyrics, folder / "work",
                )
                self.assertEqual(len(guides), 1)
                self.assertTrue(guides[0].exists())
                fields = send.call_args.args[1]
                self.assertEqual(fields["audio_cover_strength"], "0.8")
                self.assertEqual(fields["seed"], "42")
                self.assertEqual(fields["use_random_seed"], "false")
                self.assertNotIn("cover_noise_strength", fields)

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
