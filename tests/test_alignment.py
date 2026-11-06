"""Regression tests for timing and multilingual lyric alignment (no model downloads)."""
import unittest

from vta.phonetics import source_syllables
from vta.ustx import (
    LyricSection, _asr_words, _split_final_repeat, _source_syllable_cells,
    _partition_words_into_sections,
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



class FourSectionAlignmentTests(unittest.TestCase):
    """A synthetic 4-section song reproducing the failure pattern: two verses,
    two choruses, an instrumental break, and similar syllable totals. No audio
    or lyrics from any original recording are checked in.
    """

    @staticmethod
    def _words(start, end, count, syllables):
        per_word = [1] * count
        for i in range(syllables - count):
            per_word[i % count] += 1
        span = (end - start) / count
        return [
            {
                "start": start + i * span,
                "end": start + (i + 1) * span,
                "word": "synthetic",
                "syllables": n,
            }
            for i, n in enumerate(per_word)
        ]

    @staticmethod
    def _lines(syllables, count):
        # One Cyrillic vowel per artificial syllable; no original song text.
        per_line = [syllables // count] * count
        for i in range(syllables % count):
            per_line[i] += 1
        return [" ".join(["ла"] * n) for n in per_line]

    def test_four_sections_do_not_cross_instrumental_break(self):
        source = (
            self._words(28.0, 56.94, 43, 77)
            + self._words(59.44, 78.42, 27, 49)
            + self._words(105.14, 134.24, 47, 84)
            + self._words(135.88, 155.76, 27, 49)
        )
        sections = [
            LyricSection("Verse 1", self._lines(78, 10)),
            LyricSection("Chorus", self._lines(49, 6)),
            LyricSection("Verse 2", self._lines(86, 10)),
            LyricSection("Chorus", self._lines(49, 6)),
        ]
        base, extra, _ = _split_final_repeat(sections, enabled=True)
        self.assertIsNone(extra)
        self.assertEqual(len(base), 4)
        windows = _partition_words_into_sections(source, base)
        self.assertEqual([len(x) for x in windows], [43, 27, 47, 27])
        self.assertEqual(
            [(round(x[0]["start"], 2), round(x[-1]["end"], 2)) for x in windows],
            [(28.0, 56.94), (59.44, 78.42),
             (105.14, 134.24), (135.88, 155.76)],
        )

if __name__ == "__main__":
    unittest.main()
