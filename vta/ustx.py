from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .common import log

UK_VOWELS = set("аеєиіїоуюяАЕЄИІЇОУЮЯ")
SRC_VOWELS = set("аеёиоуыэюяіїєАЕЁИОУЫЭЮЯІЇЄ")
WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁёІіЇїЄєҐґ'’\-]+")
SECTION_RE = re.compile(r"^\s*\[([^]]+)\]\s*$")
HEADING_RE = re.compile(
    r"^\s*(?:[IVX]+\s+)?(?:куплет|приспів|припев|verse|chorus|bridge)\s*:?.*$",
    re.IGNORECASE,
)


@dataclass
class LyricSection:
    label: str
    lines: list[str]


@dataclass
class LineTiming:
    start: float
    end: float
    words: list[dict[str, Any]]
    word_start: int
    word_end: int


def normalize_synthesis_word(word: str) -> str:
    word = (
        word.replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", "")
        .replace("\ufeff", "")
        .replace("’", "'")
    )
    if word == "I":
        return "І"
    return word


def parse_lyrics(path: Path) -> list[LyricSection]:
    raw_lines = path.read_text(encoding="utf-8-sig").splitlines()
    sections: list[LyricSection] = []
    current = LyricSection(label="Song", lines=[])
    sections.append(current)

    for raw in raw_lines:
        line = raw.strip()
        if not line:
            continue
        m = SECTION_RE.match(line)
        if m:
            current = LyricSection(label=m.group(1).strip(), lines=[])
            sections.append(current)
            continue
        if HEADING_RE.match(line):
            current = LyricSection(label=line.rstrip(":").strip(), lines=[])
            sections.append(current)
            continue
        if len(line) >= 2 and line.startswith("«") and line.endswith("»"):
            continue
        current.lines.append(line)

    return [s for s in sections if s.lines]


def words_for_line(line: str) -> list[str]:
    return [normalize_synthesis_word(w) for w in WORD_RE.findall(line)]


def ukrainian_syllables(word: str) -> list[str]:
    word = normalize_synthesis_word(word)
    if "-" in word:
        out: list[str] = []
        for part in word.split("-"):
            if part:
                out.extend(ukrainian_syllables(part))
        return out or [word]

    vowel_pos = [i for i, ch in enumerate(word) if ch in UK_VOWELS]
    if len(vowel_pos) <= 1:
        return [word]

    out: list[str] = []
    start = 0
    for left_v, right_v in zip(vowel_pos, vowel_pos[1:]):
        cluster_start = left_v + 1
        cluster_end = right_v
        cluster_len = max(0, cluster_end - cluster_start)

        if cluster_len == 0:
            boundary = right_v
        elif cluster_len == 1:
            boundary = cluster_start
        else:
            boundary = cluster_end - 1
            while boundary > cluster_start and word[boundary] in "ь'’":
                boundary -= 1

        if boundary <= start:
            boundary = right_v
        chunk = word[start:boundary]
        if chunk:
            out.append(chunk)
        start = boundary

    tail = word[start:]
    if tail:
        out.append(tail)
    return out or [word]


def singing_units_for_line(line: str) -> list[str]:
    units: list[str] = []
    for word in words_for_line(line):
        units.extend(ukrainian_syllables(word))
    return units


def count_target_syllables(line: str) -> int:
    return max(1, len(singing_units_for_line(line)))


def count_source_syllables(text: str) -> int:
    chars = [c for c in text if c in SRC_VOWELS]
    return max(1, len(chars))


def section_key(label: str) -> str:
    s = label.casefold()
    if "chorus" in s or "присп" in s or "прип" in s:
        return "chorus"
    if "verse" in s or "куплет" in s:
        return "verse"
    if "bridge" in s or "бридж" in s:
        return "bridge"
    return re.sub(r"\W+", "", s)


def _asr_words(asr: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not asr:
        return []
    words: list[dict[str, Any]] = []
    for item in asr.get("words", []) or []:
        try:
            start = float(item["start"])
            end = float(item["end"])
        except Exception:
            continue
        text = str(item.get("word", "")).strip()
        if not text or end <= start:
            continue
        words.append({
            "start": start,
            "end": end,
            "word": text,
            "probability": float(item.get("probability", 0.0) or 0.0),
            "syllables": count_source_syllables(text),
        })
    words.sort(key=lambda w: (w["start"], w["end"]))
    return words


def align_lines_to_asr_words(
    lines: list[str],
    asr: dict[str, Any] | None,
) -> list[LineTiming]:
    words = _asr_words(asr)
    if not lines:
        return []
    if not words:
        raise RuntimeError(
            "Whisper returned no word timestamps. v7.2 requires ASR timing "
            "to keep lyrics out of instrumental sections."
        )

    n_lines = len(lines)
    n_words = len(words)
    if n_words < n_lines:
        raise RuntimeError(
            f"Not enough ASR words for lyric alignment: {n_words} words "
            f"for {n_lines} target lines."
        )

    line_weights = [count_target_syllables(line) for line in lines]
    total_target = float(sum(line_weights))

    prefix_syll = [0]
    for w in words:
        prefix_syll.append(prefix_syll[-1] + int(w["syllables"]))
    total_source = float(prefix_syll[-1])

    expected = [
        max(1.0, total_source * weight / total_target)
        for weight in line_weights
    ]

    inf = 1e30
    dp = [[inf] * (n_words + 1) for _ in range(n_lines + 1)]
    prev = [[-1] * (n_words + 1) for _ in range(n_lines + 1)]
    dp[0][0] = 0.0

    for li in range(1, n_lines + 1):
        exp = expected[li - 1]
        min_end = li
        max_end = n_words - (n_lines - li)
        for end in range(min_end, max_end + 1):
            start_min = li - 1
            start_max = end - 1
            # Keep the search local enough to prevent one line from swallowing
            # an entire verse when Whisper hallucinates extra words.
            max_word_span = max(3, int(math.ceil(exp * 2.5 + 5)))
            start_min = max(start_min, end - max_word_span)

            for start in range(start_min, start_max + 1):
                if dp[li - 1][start] >= inf:
                    continue

                src_syll = prefix_syll[end] - prefix_syll[start]
                mismatch = ((src_syll - exp) ** 2) / max(1.0, exp)

                # Prefer a line boundary at a real vocal pause.
                if end < n_words:
                    gap = max(0.0, words[end]["start"] - words[end - 1]["end"])
                    boundary_penalty = 3.0 * math.exp(-gap / 0.20)
                else:
                    boundary_penalty = 0.0

                # Avoid absurdly long lyric lines caused by ASR hallucinations.
                dur = words[end - 1]["end"] - words[start]["start"]
                duration_penalty = max(0.0, dur - 12.0) ** 2 * 0.15

                cost = (
                    dp[li - 1][start]
                    + mismatch
                    + boundary_penalty
                    + duration_penalty
                )
                if cost < dp[li][end]:
                    dp[li][end] = cost
                    prev[li][end] = start

    if prev[n_lines][n_words] < 0:
        raise RuntimeError("Could not align target lyric lines to ASR word timings.")

    ranges: list[tuple[int, int]] = []
    end = n_words
    for li in range(n_lines, 0, -1):
        start = prev[li][end]
        ranges.append((start, end))
        end = start
    ranges.reverse()

    result: list[LineTiming] = []
    for start, end in ranges:
        chunk = words[start:end]
        result.append(LineTiming(
            start=float(chunk[0]["start"]),
            end=float(chunk[-1]["end"]),
            words=chunk,
            word_start=start,
            word_end=end,
        ))
    return result


def _source_syllable_cells(words: list[dict[str, Any]]) -> list[tuple[float, float]]:
    cells: list[tuple[float, float]] = []
    for word in words:
        start = float(word["start"])
        end = float(word["end"])
        count = max(1, int(word.get("syllables", 1)))
        span = max(0.04, end - start)
        for i in range(count):
            a = start + span * (i / count)
            b = start + span * ((i + 1) / count)
            if b <= a:
                b = a + 0.04
            cells.append((a, b))
    return cells


def _map_units_to_cells(
    units: list[str],
    cells: list[tuple[float, float]],
) -> list[tuple[str, float, float]]:
    if not units:
        return []
    if not cells:
        raise RuntimeError("No source timing cells available for target syllables.")

    assignments: dict[int, list[int]] = {}
    n_src = len(cells)
    n_tgt = len(units)

    for ti in range(n_tgt):
        # Midpoint resampling. Multiple target syllables may intentionally map
        # to the same source cell; that cell is subdivided below.
        src_idx = min(
            n_src - 1,
            int(((ti + 0.5) * n_src) / n_tgt),
        )
        assignments.setdefault(src_idx, []).append(ti)

    result: list[tuple[str, float, float] | None] = [None] * n_tgt
    for src_idx, target_indices in assignments.items():
        a, b = cells[src_idx]
        span = max(0.05, b - a)
        count = len(target_indices)
        for j, ti in enumerate(target_indices):
            start = a + span * (j / count)
            end = a + span * ((j + 1) / count)
            if end - start < 0.045:
                end = start + 0.045
            result[ti] = (units[ti], start, end)

    return [x for x in result if x is not None]


def _tone_for_interval(
    start: float,
    end: float,
    source_notes: list[dict[str, Any]],
    fallback: int = 60,
) -> int:
    weighted: list[tuple[float, float]] = []
    center = (start + end) * 0.5

    for note in source_notes:
        ns = float(note["start"])
        ne = float(note["end"])
        overlap = max(0.0, min(end, ne) - max(start, ns))
        if overlap > 0:
            weighted.append((float(note.get("midi_float", note["midi"])), overlap))

    if weighted:
        expanded = []
        for midi, weight in weighted:
            expanded.append((midi, weight))
        expanded.sort(key=lambda x: x[0])
        total = sum(w for _, w in expanded)
        acc = 0.0
        for midi, weight in expanded:
            acc += weight
            if acc >= total * 0.5:
                return int(max(24, min(96, round(midi))))

    if source_notes:
        nearest = min(
            source_notes,
            key=lambda n: abs(
                ((float(n["start"]) + float(n["end"])) * 0.5) - center
            ),
        )
        return int(max(24, min(96, round(float(nearest.get("midi_float", nearest["midi"]))))))

    return fallback


def _build_line_notes(
    line: str,
    timing: LineTiming,
    source_notes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    units = singing_units_for_line(line)
    cells = _source_syllable_cells(timing.words)
    mapped = _map_units_to_cells(units, cells)

    out: list[dict[str, Any]] = []
    previous_tone = 60
    for lyric, start, end in mapped:
        tone = _tone_for_interval(start, end, source_notes, fallback=previous_tone)
        previous_tone = tone
        out.append({
            "start": start,
            "end": end,
            "midi": tone,
            "midi_float": float(tone),
            "lyric": lyric,
        })
    return out


def _shift_timing(timing: LineTiming, shift: float) -> LineTiming:
    words = []
    for w in timing.words:
        x = dict(w)
        x["start"] = float(w["start"]) + shift
        x["end"] = float(w["end"]) + shift
        words.append(x)
    return LineTiming(
        start=timing.start + shift,
        end=timing.end + shift,
        words=words,
        word_start=timing.word_start,
        word_end=timing.word_end,
    )


def _shift_source_notes(
    source_notes: list[dict[str, Any]],
    start: float,
    end: float,
    shift: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for n in source_notes:
        ns = float(n["start"])
        ne = float(n["end"])
        if ne <= start or ns >= end:
            continue
        x = dict(n)
        x["start"] = ns + shift
        x["end"] = ne + shift
        out.append(x)
    return out


def _make_ustx_note(
    note: dict[str, Any],
    ticks_per_second: float,
) -> dict[str, Any]:
    start = int(round(float(note["start"]) * ticks_per_second))
    end = int(round(float(note["end"]) * ticks_per_second))
    duration = max(20, end - start)
    return {
        "position": start,
        "duration": duration,
        "tone": int(note["midi"]),
        "lyric": str(note["lyric"]),
        "pitch": {
            "data": [
                {"x": -40.0, "y": 0.0, "shape": "io"},
                {"x": 0.0, "y": 0.0, "shape": "io"},
            ],
            "snap_first": True,
        },
        "vibrato": {},
        "tuning": 0,
        "phoneme_expressions": [],
        "phoneme_overrides": [],
    }


def build_ustx(
    cfg: dict[str, Any],
    lyrics_path: Path,
    melody: dict[str, Any],
    asr: dict[str, Any] | None,
    track_work: Path,
) -> tuple[Path, dict[str, Any] | None]:
    sections = parse_lyrics(lyrics_path)
    if not sections:
        raise RuntimeError("Target lyrics contain no singable lines.")

    source_notes = [dict(n) for n in melody.get("notes", [])]
    if not source_notes:
        raise RuntimeError("No melody notes were extracted from the source vocal.")

    append_repeat = bool(cfg.get("append_final_repeated_section", True))
    final_repeat: LyricSection | None = None
    previous_repeat_index: int | None = None

    if append_repeat and len(sections) >= 2:
        key = section_key(sections[-1].label)
        if key == "chorus":
            for idx in range(len(sections) - 2, -1, -1):
                if section_key(sections[idx].label) == key:
                    final_repeat = sections[-1]
                    previous_repeat_index = idx
                    break

    base_sections = sections[:-1] if final_repeat is not None else sections
    base_lines = [line for sec in base_sections for line in sec.lines]

    timings = align_lines_to_asr_words(base_lines, asr)
    if len(timings) != len(base_lines):
        raise RuntimeError("Internal error: ASR line timing count mismatch.")

    generated_notes: list[dict[str, Any]] = []
    line_debug: list[dict[str, Any]] = []
    for index, (line, timing) in enumerate(zip(base_lines, timings)):
        notes = _build_line_notes(line, timing, source_notes)
        generated_notes.extend(notes)
        line_debug.append({
            "line_index": index,
            "text": line,
            "start": timing.start,
            "end": timing.end,
            "source_word_start": timing.word_start,
            "source_word_end": timing.word_end,
            "target_syllables": len(singing_units_for_line(line)),
            "generated_notes": len(notes),
        })

    repeat_plan: dict[str, Any] | None = None

    if final_repeat is not None and previous_repeat_index is not None:
        line_start = sum(len(s.lines) for s in base_sections[:previous_repeat_index])
        line_end = line_start + len(base_sections[previous_repeat_index].lines)

        if line_start < len(timings) and line_end <= len(timings):
            template_timings = timings[line_start:line_end]
            template_start = template_timings[0].start
            template_end = template_timings[-1].end

            song_duration = float(melody.get("duration", source_notes[-1]["end"]))
            gap_s = float(cfg.get("repeat_section_gap_s", 0.45))
            dest_start = song_duration + gap_s
            shift = dest_start - template_start

            shifted_source_notes = _shift_source_notes(
                source_notes,
                template_start,
                template_end,
                shift,
            )

            for i, line in enumerate(final_repeat.lines):
                template_timing = template_timings[
                    min(i, len(template_timings) - 1)
                ]
                shifted_timing = _shift_timing(template_timing, shift)
                notes = _build_line_notes(
                    line,
                    shifted_timing,
                    shifted_source_notes,
                )
                generated_notes.extend(notes)
                line_debug.append({
                    "line_index": len(base_lines) + i,
                    "text": line,
                    "start": shifted_timing.start,
                    "end": shifted_timing.end,
                    "source_word_start": template_timing.word_start,
                    "source_word_end": template_timing.word_end,
                    "target_syllables": len(singing_units_for_line(line)),
                    "generated_notes": len(notes),
                    "repeated": True,
                })

            repeat_plan = {
                "enabled": True,
                "section": final_repeat.label,
                "source_start": template_start,
                "source_end": template_end,
                "dest_start": dest_start,
                "dest_end": dest_start + (template_end - template_start),
            }
            log(
                "[Structure] Appending final repeated chorus using ASR-anchored "
                f"timing ({template_start:.2f}-{template_end:.2f}s -> "
                f"{dest_start:.2f}s)."
            )

    generated_notes.sort(key=lambda n: (n["start"], n["end"]))

    # Safety: do not allow overlapping lyric notes. Shorten the earlier one;
    # never move the next lyric into an instrumental pause.
    for i in range(len(generated_notes) - 1):
        cur = generated_notes[i]
        nxt = generated_notes[i + 1]
        if float(cur["end"]) > float(nxt["start"]):
            cur["end"] = max(float(cur["start"]) + 0.04, float(nxt["start"]))

    bpm = float(cfg.get("ustx_bpm", 120.0))
    ticks_per_second = 480.0 * bpm / 60.0
    ustx_notes = [
        _make_ustx_note(note, ticks_per_second)
        for note in generated_notes
        if float(note["end"]) > float(note["start"])
    ]

    if not ustx_notes:
        raise RuntimeError("No syllable notes were generated for DiffSinger.")

    end_tick = max(n["position"] + n["duration"] for n in ustx_notes) + 960
    project = {
        "name": "Vocal Translate AI - Ukrainian Guide",
        "comment": (
            "v7.2 ASR-anchored syllable timing. "
            "Source lyrics file is never modified."
        ),
        "output_dir": "Vocal",
        "cache_dir": "UCache",
        "ustx_version": "0.10",
        "time_signatures": [
            {"bar_position": 0, "beat_per_bar": 4, "beat_unit": 4}
        ],
        "tempos": [{"position": 0, "bpm": bpm}],
        "tracks": [
            {
                "singer": "",
                "phonemizer": (
                    "OpenUtau.Core.DiffSinger."
                    "DiffSingerUkrainianPhonemizer"
                ),
                "renderer_settings": {"renderer": "DIFFSINGER"},
                "track_name": "Ukrainian Guide",
                "track_color": "Blue",
                "mute": False,
                "solo": False,
                "volume": 0.0,
                "pan": 0.0,
                "voice_color_names": [""],
            }
        ],
        "voice_parts": [
            {
                "name": "Ukrainian Guide",
                "comment": "",
                "track_no": 0,
                "position": 0,
                "duration": end_tick,
                "notes": ustx_notes,
                "curves": [],
                "masked_curves": [],
            }
        ],
        "wave_parts": [],
    }

    out_dir = track_work / "diffsinger"
    out_dir.mkdir(parents=True, exist_ok=True)
    ustx_path = out_dir / "guide.ustx"
    ustx_path.write_text(
        yaml.safe_dump(
            project,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        ),
        encoding="utf-8",
    )

    alignment = {
        "mode": "asr_word_timing_to_ukrainian_syllables",
        "sections": [
            {"label": s.label, "lines": s.lines}
            for s in sections
        ],
        "lines": line_debug,
        "repeat_plan": repeat_plan,
        "source_f0_note_count": len(source_notes),
        "generated_syllable_note_count": len(ustx_notes),
    }
    (out_dir / "alignment.json").write_text(
        json.dumps(alignment, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log(
        "[DiffSinger] v7.2 ASR-anchored USTX generated: "
        f"{len(ustx_notes)} syllable notes -> {ustx_path}"
    )
    return ustx_path, repeat_plan
