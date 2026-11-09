"""Pitch confidence regression tests using synthetic F0 events only."""
import unittest

from vta.ustx import _pitch_events_for_syllable, _build_word_notes, LineTiming


class PitchQualityTests(unittest.TestCase):
    def test_noisy_low_confidence_syllable_is_one_sustained_note(self):
        notes = [
            {"start": 0.0, "end": 0.20, "midi": 58, "confidence": 0.05},
            {"start": 0.20, "end": 0.40, "midi": 72, "confidence": 0.09},
            {"start": 0.40, "end": 0.65, "midi": 54, "confidence": 0.03},
        ]
        events = _pitch_events_for_syllable(
            0.0, 0.65, notes, previous_tone=59,
            max_events=3, min_event_s=0.09, min_confidence=0.22,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["start"], 0)
        self.assertAlmostEqual(events[0]["end"], 0.65)

    def test_confident_two_pitch_melisma_remains(self):
        notes = [
            {"start": 0.0, "end": 0.32, "midi": 60, "confidence": 0.8},
            {"start": 0.32, "end": 0.70, "midi": 65, "confidence": 0.8},
        ]
        events = _pitch_events_for_syllable(
            0.0, 0.70, notes, previous_tone=60,
            max_events=2, min_event_s=0.16, min_confidence=0.22,
        )
        self.assertEqual(len(events), 2)
        self.assertEqual([round(x["tone"]) for x in events], [60, 65])

    def test_confident_small_pitch_wobble_merged(self):
        notes = [
            {"start": 0.0, "end": 0.25, "midi": 60, "confidence": 0.85},
            {"start": 0.25, "end": 0.50, "midi": 61, "confidence": 0.82},
        ]
        events = _pitch_events_for_syllable(
            0.0, 0.50, notes, previous_tone=60,
            max_events=2, min_event_s=0.15, min_change_semitones=2.0,
        )
        self.assertEqual(len(events), 1)

    def test_phrase_starts_on_source_note_not_fixed_c4(self):
        timing = LineTiming(
            start=10.0, end=11.0,
            words=[{"start": 10.0, "end": 11.0,
                    "word": "спів", "syllables": 1}],
            word_start=0, word_end=1,
        )
        notes = [{"start": 10.0, "end": 11.0, "midi": 73, "confidence": 0.8}]
        generated = _build_word_notes("спів", timing, notes, {})
        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0]["midi"], 73)


if __name__ == "__main__":
    unittest.main()
