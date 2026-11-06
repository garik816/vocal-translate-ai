"""Regression tests for timing and multilingual lyric alignment (no model downloads)."""
import unittest

from vta.phonetics import source_syllables
from vta.ustx import (
    LyricSection, _asr_words, _split_final_repeat, _source_syllable_cells,
)


class SourceSyllableTests(unittest.TestCase):
    def test_english_words_are_not_all_one_syllable(self):
        self.assertEqual(source_syllables("hello", "en"), 2)
        self.assertEqual(source_syllables("computer", "en"), 3)
        self.assertEqual(source_syllables("singing", "en"), 2)

    def test_cyrillic_counts(self):
        self.assertEqual(source_syllables("Доброго", "uk"), 3)
        self.assertEqual(source_syllables("привіт", "uk"), 2)

    def test_empty_or_punctuation(self):
        self.assertEqual(source_syllables("...", "en"), 1)
        self.assertEqual(source_syllables("", "en"), 1)

    def test_asr_language_is_used_in_alignment(self):
        words = _asr_words({
            "language": "en",
            "words": [
                {"start": 1.0, "end": 1.4, "word": "hello", "probability": 0.9},
                {"start": 1.4, "end": 2.0, "word": "computer", "probability": 0.9},
            ],
        })
        self.assertEqual([word["syllables"] for word in words], [2, 3])
        self.assertEqual(len(_source_syllable_cells(words)), 5)


class ChorusRepeatTests(unittest.TestCase):
    def setUp(self):
        self.verse = LyricSection("Verse", ["Один рядок"])
        self.first_chorus = LyricSection("Chorus", ["Перший рядок", "Другий рядок"])

    def test_last_normal_chorus_is_never_implicitly_duplicate(self):
        sections = [self.verse, self.first_chorus,
                    LyricSection("Chorus", ["Інший рядок", "Ще рядок"])]
        base, extra, idx = _split_final_repeat(sections, enabled=True)
        self.assertEqual(base, sections)
        self.assertIsNone(extra)
        self.assertIsNone(idx)

    def test_explicit_repeat_is_enabled(self):
        extra_chorus = LyricSection("Chorus repeat", ["Третій рядок", "Четвертий рядок"])
        sections = [self.verse, self.first_chorus, extra_chorus]
        base, extra, idx = _split_final_repeat(sections, enabled=True)
        self.assertEqual(base, sections[:-1])
        self.assertEqual(extra, extra_chorus)
        self.assertEqual(idx, 1)

    def test_explicit_repeat_requires_config(self):
        sections = [self.verse, self.first_chorus,
                    LyricSection("Chorus repeat", ["Третій рядок", "Четвертий рядок"])]
        base, extra, idx = _split_final_repeat(sections, enabled=False)
        self.assertEqual(base, sections)
        self.assertIsNone(extra)
        self.assertIsNone(idx)


if __name__ == "__main__":
    unittest.main()
