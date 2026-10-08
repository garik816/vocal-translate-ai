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


def target_syllables(word: str) -> int:
    return max(1, sum(1 for ch in word if ch in UK_VOWELS))


def source_syllables(word: str) -> int:
    return max(1, sum(1 for ch in word if ch in SRC_VOWELS))


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
    out: list[dict[str, Any]] = []
    for item in asr.get("words", []) or []:
        try:
            start = float(item["start"])
            end = float(item["end"])
        except Exception:
            continue
        text = str(item.get("word", "")).strip()
        if not text or end <= start:
            continue
        out.append({
            "start": start,
            "end": end,
            "word": text,
            "probability": float(item.get("probability", 0.0) or 0.0),
            "syllables": source_syllables(text),
        })
    out.sort(key=lambda w: (w["start"], w["end"]))
    return out


def _trim_trailing_outro(
    words: list[dict[str, Any]],
    gap_s: float,
) -> list[dict[str, Any]]:
    if len(words) < 6 or gap_s <= 0:
        return words

    cut = None
    for i in range(1, len(words)):
        gap = float(words[i]["start"]) - float(words[i - 1]["end"])
        tail_ratio = (len(words) - i) / len(words)
        if gap >= gap_s and tail_ratio <= 0.25:
            cut = i

    if cut is None:
        return words

    log(
        "[Alignment] Trailing source outro excluded from base lyrics: "
        f"gap={words[cut]['start'] - words[cut - 1]['end']:.2f}s, "
        f"tail_words={len(words) - cut}."
    )
    return words[:cut]


def _partition_words_into_sections(
    words: list[dict[str, Any]],
    section_count: int,
) -> list[list[dict[str, Any]]]:
    if section_count <= 1:
        return [words]
    if len(words) < section_count:
        raise RuntimeError(
            f"ASR produced {len(words)} words for {section_count} lyric sections."
        )

    gaps: list[tuple[float, int]] = []
    for i in range(1, len(words)):
        gap = max(0.0, float(words[i]["start"]) - float(words[i - 1]["end"]))
        gaps.append((gap, i))

    chosen = sorted(
        i for _, i in sorted(gaps, reverse=True)[: section_count - 1]
    )
    bounds = [0] + chosen + [len(words)]
    regions = [words[bounds[i]:bounds[i + 1]] for i in range(section_count)]

    log(
        "[Alignment] Section boundaries from source vocal pauses: "
        + ", ".join(
            f"{words[i - 1]['end']:.2f}->{words[i]['start']:.2f}s"
            for i in chosen
        )
    )
    return regions


def _align_lines_within_section(
    lines: list[str],
    words: list[dict[str, Any]],
) -> list[LineTiming]:
    if not lines:
        return []
    if len(words) < len(lines):
        raise RuntimeError(
            f"Section has only {len(words)} ASR words for {len(lines)} target lines."
        )

    target_weights = [
        max(1, sum(target_syllables(w) for w in words_for_line(line)))
        for line in lines
    ]
    prefix = [0]
    for w in words:
        prefix.append(prefix[-1] + int(w["syllables"]))

    total_source = float(prefix[-1])
    total_target = float(sum(target_weights))
    expected = [
        max(1.0, total_source * weight / total_target)
        for weight in target_weights
    ]

    n_lines = len(lines)
    n_words = len(words)
    inf = 1e30
    dp = [[inf] * (n_words + 1) for _ in range(n_lines + 1)]
    prev = [[-1] * (n_words + 1) for _ in range(n_lines + 1)]
    dp[0][0] = 0.0

    for li in range(1, n_lines + 1):
        exp = expected[li - 1]
        for end in range(li, n_words - (n_lines - li) + 1):
            start_lo = li - 1
            start_hi = end - 1
            max_span = max(4, int(math.ceil(exp * 2.2 + 4)))
            start_lo = max(start_lo, end - max_span)

            for start in range(start_lo, start_hi + 1):
                if dp[li - 1][start] >= inf:
                    continue

                src_syll = prefix[end] - prefix[start]
                mismatch = ((src_syll - exp) ** 2) / max(1.0, exp)

                if end < n_words:
                    gap = max(
                        0.0,
                        float(words[end]["start"]) - float(words[end - 1]["end"]),
                    )
                    boundary_penalty = 2.5 * math.exp(-gap / 0.18)
                else:
                    boundary_penalty = 0.0

                duration = float(words[end - 1]["end"]) - float(words[start]["start"])
                duration_penalty = max(0.0, duration - 11.0) ** 2 * 0.20

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
        raise RuntimeError("Could not align lyric lines inside a source section.")

    ranges: list[tuple[int, int]] = []
    end = n_words
    for li in range(n_lines, 0, -1):
        start = prev[li][end]
        ranges.append((start, end))
        end = start
    ranges.reverse()

    out: list[LineTiming] = []
    for start, end in ranges:
        chunk = words[start:end]
        out.append(LineTiming(
            start=float(chunk[0]["start"]),
            end=float(chunk[-1]["end"]),
            words=chunk,
            word_start=start,
            word_end=end,
        ))
    return out


def _source_syllable_cells(
    words: list[dict[str, Any]],
) -> list[tuple[float, float]]:
    cells: list[tuple[float, float]] = []
    for word in words:
        start = float(word["start"])
        end = float(word["end"])
        count = max(1, int(word.get("syllables", 1)))
        span = max(0.06, end - start)
        for i in range(count):
            a = start + span * (i / count)
            b = start + span * ((i + 1) / count)
            cells.append((a, max(a + 0.04, b)))
    return cells


def _time_at_cell_fraction(
    cells: list[tuple[float, float]],
    fraction: float,
) -> float:
    if not cells:
        raise RuntimeError("No source timing cells.")
    fraction = max(0.0, min(1.0, fraction))
    if fraction <= 0:
        return float(cells[0][0])
    if fraction >= 1:
        return float(cells[-1][1])

    pos = fraction * len(cells)
    idx = min(len(cells) - 1, int(math.floor(pos)))
    local = pos - idx
    a, b = cells[idx]
    return float(a + (b - a) * local)


def _target_word_intervals(
    line: str,
    timing: LineTiming,
) -> list[tuple[str, float, float]]:
    target_words = words_for_line(line)
    if not target_words:
        return []

    cells = _source_syllable_cells(timing.words)
    weights = [target_syllables(word) for word in target_words]
    total = float(sum(weights))

    out: list[tuple[str, float, float]] = []
    acc = 0.0
    for word, weight in zip(target_words, weights):
        start = _time_at_cell_fraction(cells, acc / total)
        acc += weight
        end = _time_at_cell_fraction(cells, acc / total)

        if end - start < 0.10:
            end = start + 0.10

        out.append((word, start, end))

    # Preserve source line edges exactly.
    first_word, _, first_end = out[0]
    out[0] = (first_word, timing.start, first_end)
    last_word, last_start, _ = out[-1]
    out[-1] = (last_word, last_start, timing.end)
    return out


def _tone_for_interval(
    start: float,
    end: float,
    source_notes: list[dict[str, Any]],
    fallback: int = 60,
) -> int:
    overlaps: list[tuple[float, float]] = []
    center = (start + end) * 0.5

    for note in source_notes:
        ns = float(note["start"])
        ne = float(note["end"])
        overlap = max(0.0, min(end, ne) - max(start, ns))
        if overlap > 0:
            overlaps.append((
                float(note.get("midi_float", note["midi"])),
                overlap,
            ))

    if overlaps:
        overlaps.sort(key=lambda x: x[0])
        total = sum(weight for _, weight in overlaps)
        acc = 0.0
        for midi, weight in overlaps:
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
        return int(max(
            24,
            min(96, round(float(nearest.get("midi_float", nearest["midi"])))),
        ))

    return fallback


def _build_word_notes(
    line: str,
    timing: LineTiming,
    source_notes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    intervals = _target_word_intervals(line, timing)
    out: list[dict[str, Any]] = []
    previous_tone = 60

    for lyric, start, end in intervals:
        tone = _tone_for_interval(start, end, source_notes, fallback=previous_tone)
        previous_tone = tone
        out.append({
            "start": start,
            "end": end,
            "midi": tone,
            "midi_float": float(tone),
            # Critical v7.3 change: whole Ukrainian word goes to G2P.
            "lyric": lyric,
        })
    return out


def _shift_timing(timing: LineTiming, shift: float) -> LineTiming:
    shifted_words: list[dict[str, Any]] = []
    for word in timing.words:
        item = dict(word)
        item["start"] = float(word["start"]) + shift
        item["end"] = float(word["end"]) + shift
        shifted_words.append(item)

    return LineTiming(
        start=timing.start + shift,
        end=timing.end + shift,
        words=shifted_words,
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
    for note in source_notes:
        ns = float(note["start"])
        ne = float(note["end"])
        if ne <= start or ns >= end:
            continue
        item = dict(note)
        item["start"] = ns + shift
        item["end"] = ne + shift
        out.append(item)
    return out


def _make_ustx_note(
    note: dict[str, Any],
    ticks_per_second: float,
) -> dict[str, Any]:
    start = int(round(float(note["start"]) * ticks_per_second))
    end = int(round(float(note["end"]) * ticks_per_second))
    duration = max(40, end - start)
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
        final_key = section_key(sections[-1].label)
        if final_key == "chorus":
            for idx in range(len(sections) - 2, -1, -1):
                if section_key(sections[idx].label) == final_key:
                    final_repeat = sections[-1]
                    previous_repeat_index = idx
                    break

    base_sections = sections[:-1] if final_repeat is not None else sections

    source_words = _trim_trailing_outro(
        _asr_words(asr),
        float(cfg.get("asr_outro_gap_s", 4.0)),
    )
    if not source_words:
        raise RuntimeError("No usable Whisper word timings for v7.3 alignment.")

    source_regions = _partition_words_into_sections(
        source_words,
        len(base_sections),
    )

    section_timings: list[list[LineTiming]] = []
    generated_notes: list[dict[str, Any]] = []
    debug_sections: list[dict[str, Any]] = []

    for section_index, (section, region) in enumerate(
        zip(base_sections, source_regions)
    ):
        timings = _align_lines_within_section(section.lines, region)
        section_timings.append(timings)

        line_debug: list[dict[str, Any]] = []
        for line_index, (line, timing) in enumerate(zip(section.lines, timings)):
            notes = _build_word_notes(line, timing, source_notes)
            generated_notes.extend(notes)
            line_debug.append({
                "line_index": line_index,
                "text": line,
                "start": timing.start,
                "end": timing.end,
                "source_words": [
                    str(w.get("word", "")) for w in timing.words
                ],
                "target_words": words_for_line(line),
                "generated_word_notes": len(notes),
            })

        debug_sections.append({
            "section_index": section_index,
            "label": section.label,
            "source_start": float(region[0]["start"]),
            "source_end": float(region[-1]["end"]),
            "source_word_count": len(region),
            "lines": line_debug,
        })

    repeat_plan: dict[str, Any] | None = None

    if (
        final_repeat is not None
        and previous_repeat_index is not None
        and previous_repeat_index < len(section_timings)
    ):
        template_timings = section_timings[previous_repeat_index]
        if template_timings:
            template_start = template_timings[0].start
            template_end = template_timings[-1].end

            song_duration = float(melody.get(
                "duration",
                source_notes[-1]["end"],
            ))
            gap_s = float(cfg.get("repeat_section_gap_s", 0.45))
            dest_start = song_duration + gap_s
            shift = dest_start - template_start

            shifted_source_notes = _shift_source_notes(
                source_notes,
                template_start,
                template_end,
                shift,
            )

            repeat_debug: list[dict[str, Any]] = []
            for i, line in enumerate(final_repeat.lines):
                template = template_timings[min(i, len(template_timings) - 1)]
                timing = _shift_timing(template, shift)
                notes = _build_word_notes(line, timing, shifted_source_notes)
                generated_notes.extend(notes)
                repeat_debug.append({
                    "line_index": i,
                    "text": line,
                    "start": timing.start,
                    "end": timing.end,
                    "target_words": words_for_line(line),
                    "generated_word_notes": len(notes),
                })

            repeat_plan = {
                "enabled": True,
                "section": final_repeat.label,
                "source_start": template_start,
                "source_end": template_end,
                "dest_start": dest_start,
                "dest_end": dest_start + (template_end - template_start),
            }
            debug_sections.append({
                "section_index": len(base_sections),
                "label": final_repeat.label,
                "repeated": True,
                "source_start": template_start,
                "source_end": template_end,
                "dest_start": dest_start,
                "dest_end": repeat_plan["dest_end"],
                "lines": repeat_debug,
            })
            log(
                "[Structure] Final chorus repeats exactly the previous chorus "
                f"window: {template_start:.2f}-{template_end:.2f}s "
                f"({template_end - template_start:.2f}s)."
            )

    generated_notes.sort(key=lambda n: (n["start"], n["end"]))

    # Prevent overlap without moving later words into silent sections.
    for i in range(len(generated_notes) - 1):
        cur = generated_notes[i]
        nxt = generated_notes[i + 1]
        if float(cur["end"]) > float(nxt["start"]):
            cur["end"] = max(float(cur["start"]) + 0.08, float(nxt["start"]))

    bpm = float(cfg.get("ustx_bpm", 120.0))
    ticks_per_second = 480.0 * bpm / 60.0
    ustx_notes = [
        _make_ustx_note(note, ticks_per_second)
        for note in generated_notes
        if float(note["end"]) - float(note["start"]) >= 0.075
    ]

    if not ustx_notes:
        raise RuntimeError("v7.3 generated no target-word notes.")

    end_tick = max(
        note["position"] + note["duration"]
        for note in ustx_notes
    ) + 960

    project = {
        "name": "Vocal Translate AI - Ukrainian Guide",
        "comment": (
            "v7.3: whole-word Ukrainian G2P, section-aware ASR timing. "
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
        "mode": "v7.3_section_aware_whole_word",
        "sections": debug_sections,
        "repeat_plan": repeat_plan,
        "source_f0_note_count": len(source_notes),
        "generated_target_word_notes": len(ustx_notes),
    }
    (out_dir / "alignment.json").write_text(
        json.dumps(alignment, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log(
        "[DiffSinger] v7.3 whole-word USTX generated: "
        f"{len(ustx_notes)} word notes -> {ustx_path}"
    )
    return ustx_path, repeat_plan
