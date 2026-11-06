"""Lightweight syllable estimates for aligning source-language sung words.

CMUdict gives reliable English counts when installed; the built-in fallback
keeps alignment working offline for other languages and unknown words.
"""
from __future__ import annotations

import re
from functools import lru_cache

CYRILLIC_VOWELS = set("аеёиоуыэюяіїєАЕЁИОУЫЭЮЯІЇЄ")
LATIN_VOWELS = "aeiouyáéíóúàèìòùâêîôûäëïöüãõåæø"


@lru_cache(maxsize=1)
def _cmu_dictionary() -> dict:
    try:
        import cmudict
    except ImportError:
        return {}
    return cmudict.dict()


def _english_word_syllables(word: str) -> int:
    pronunciations = _cmu_dictionary().get(word)
    if pronunciations:
        # One vowel phoneme per syllable; stress digits mark vowel phonemes.
        counts = [sum(any(c.isdigit() for c in phoneme) for phoneme in p)
                  for p in pronunciations]
        valid = [count for count in counts if count]
        if valid:
            return min(valid)

    groups = re.findall(r"[aeiouy]+", word)
    count = len(groups)
    # Unstressed final e is usually silent, except in consonant + le (table).
    if (count > 1 and word.endswith("e")
            and groups[-1] == "e"
            and not re.search(r"[^aeiouy]le$", word)):
        count -= 1
    return max(1, count)


def source_syllables(word: str, language: str = "") -> int:
    """Estimate source syllables without treating every Latin word as one.

    This is an alignment heuristic, NOT a phonetic transcription.
    """
    word = word.lower().replace("’", "'").strip()
    if not word:
        return 1
    if any(char in CYRILLIC_VOWELS for char in word):
        return max(1, sum(char in CYRILLIC_VOWELS for char in word))

    parts = re.findall(r"[a-záéíóúàèìòùâêîôûäëïöüãõåæø]+", word)
    if not parts:
        return 1
    if language.lower().startswith("en") or not language:
        return sum(_english_word_syllables(part) for part in parts)
    return sum(max(1, len(re.findall(r"[" + LATIN_VOWELS + r"]+", part)))
               for part in parts)
