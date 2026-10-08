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
WORD_RE = re.compile(r"[A-Za-zА-Яа-яІіЇїЄєҐґ'’\-]+")
SECTION_RE = re.compile(r"^\s*\[([^]]+)\]\s*$")
HEADING_RE = re.compile(
    r"^\s*(?:[IVX]+\s+)?(?:куплет|приспів|припев|verse|chorus|bridge)\s*:?.*$",
    re.IGNORECASE,
)


@dataclass
class LyricSection:
    label: str
    lines: list[str]


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
            # Treat a standalone quoted title as metadata, not a sung line.
            continue
        current.lines.append(line)

    return [s for s in sections if s.lines]


def words_for_line(line: str) -> list[str]:
    return [normalize_synthesis_word(w) for w in WORD_RE.findall(line)]


def syllables(word: str) -> int:
    n = sum(1 for ch in word if ch in UK_VOWELS)
    return max(1, n)


def ukrainian_syllables(word: str) -> list[str]:
    """Split a Ukrainian orthographic word into singable syllable chunks.

    This is synthesis-only: the user's source TXT is never changed.
    DiffSinger needs one lexical syllable per onset note; +~ is reserved
    strictly for extending that syllable over connected pitch notes.
    """
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
        # Consonants between two vowels. Prefer an open syllable:
        # one consonant goes with the following vowel; in a cluster,
        # keep only the final onset consonant with the next syllable.
        cluster_start = left_v + 1
        cluster_end = right_v
        cluster_len = max(0, cluster_end - cluster_start)

        if cluster_len == 0:
            boundary = right_v
        elif cluster_len == 1:
            boundary = cluster_start
        else:
            boundary = cluster_end - 1
            # Do not begin a syllable with a soft sign/apostrophe.
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


def section_key(label: str) -> str:
    s = label.casefold()
    if "chorus" in s or "присп" in s or "прип" in s:
        return "chorus"
    if "verse" in s or "куплет" in s:
        return "verse"
    if "bridge" in s or "бридж" in s:
        return "bridge"
    return re.sub(r"\W+", "", s)


def _asr_end_times(asr: dict[str, Any] | None) -> list[float]:
    if not asr:
        return []
    out = []
    for seg in asr.get("segments", []) or []:
        try:
            out.append(float(seg["end"]))
        except Exception:
            pass
    return out


def align_lines_to_notes(
    lines: list[str],
    notes: list[dict[str, Any]],
    asr: dict[str, Any] | None = None,
) -> list[tuple[int, int]]:
    """
    Dynamic-programming partition of note events into lyric lines.

    Each result is [start_note_index, end_note_index), covering all notes once.
    Cost balances syllable count with natural pauses and ASR segment ends.
    """
    n_lines = len(lines)
    n_notes = len(notes)
    if n_lines == 0 or n_notes == 0:
        return []
    if n_notes < n_lines:
        # Still produce monotonic slices; a few lines may share a note later.
        return [
            (
                min(n_notes - 1, int(round(i * n_notes / n_lines))),
                min(n_notes, max(1, int(round((i + 1) * n_notes / n_lines)))),
            )
            for i in range(n_lines)
        ]

    weights = [
        max(1, sum(syllables(w) for w in words_for_line(line)))
        for line in lines
    ]
    total_w = sum(weights)
    expected = [n_notes * w / total_w for w in weights]
    asr_ends = _asr_end_times(asr)

    # boundary_cost[k] is the cost of ending a line after note k-1.
    boundary_cost = [0.0] * (n_notes + 1)
    for k in range(1, n_notes):
        gap = max(0.0, float(notes[k]["start"]) - float(notes[k - 1]["end"]))
        cost = 3.5 * math.exp(-gap / 0.22)
        boundary_t = float(notes[k - 1]["end"])
        if asr_ends:
            nearest = min(abs(boundary_t - t) for t in asr_ends)
            if nearest < 0.30:
                cost *= 0.45
            elif nearest < 0.65:
                cost *= 0.75
        boundary_cost[k] = cost

    inf = 1e30
    dp = [[inf] * (n_notes + 1) for _ in range(n_lines + 1)]
    prev = [[-1] * (n_notes + 1) for _ in range(n_lines + 1)]
    dp[0][0] = 0.0

    for line_i in range(1, n_lines + 1):
        exp_len = expected[line_i - 1]
        min_end = line_i
        max_end = n_notes - (n_lines - line_i)
        for end in range(min_end, max_end + 1):
            start_lo = line_i - 1
            start_hi = end - 1
            # Restrict search to a generous band around expected note count.
            max_len = max(8, int(math.ceil(exp_len * 3.0 + 8)))
            start_lo = max(start_lo, end - max_len)
            for start in range(start_lo, start_hi + 1):
                if dp[line_i - 1][start] >= inf:
                    continue
                count = end - start
                mismatch = ((count - exp_len) ** 2) / max(1.0, exp_len)
                cost = dp[line_i - 1][start] + mismatch + boundary_cost[end]
                if cost < dp[line_i][end]:
                    dp[line_i][end] = cost
                    prev[line_i][end] = start

    if prev[n_lines][n_notes] < 0:
        # Deterministic proportional fallback.
        cuts = [0]
        acc = 0.0
        for i in range(n_lines - 1):
            acc += weights[i] / total_w
            cuts.append(max(cuts[-1] + 1, min(n_notes - (n_lines - i - 1), round(acc * n_notes))))
        cuts.append(n_notes)
        return [(cuts[i], cuts[i + 1]) for i in range(n_lines)]

    ranges: list[tuple[int, int]] = []
    end = n_notes
    for line_i in range(n_lines, 0, -1):
        start = prev[line_i][end]
        ranges.append((start, end))
        end = start
    ranges.reverse()
    return ranges


def distribute_units(
    units: list[str],
    notes: list[dict[str, Any]],
    max_slur_gap_s: float,
) -> list[str]:
    note_count = len(notes)
    if note_count <= 0:
        return []
    if not units:
        return ["a"] * note_count

    # In the unusual case where there are fewer pitch notes than syllables,
    # keep all text by joining adjacent syllables on the available notes.
    # This is preferable to silently dropping target text.
    if note_count < len(units):
        groups = [[] for _ in range(note_count)]
        for i, unit in enumerate(units):
            idx = min(note_count - 1, int(i * note_count / len(units)))
            groups[idx].append(unit)
        return ["".join(g) if g else "a" for g in groups]

    # Give every syllable at least one onset note. Extra pitch notes are
    # distributed across syllables; they become +~ only when physically
    # connected to the preceding note.
    counts = [1] * len(units)
    extra = note_count - len(units)
    for i in range(extra):
        counts[i % len(counts)] += 1

    lyrics: list[str] = []
    note_index = 0
    for unit, count in zip(units, counts):
        lyrics.append(unit)
        note_index += 1
        for _ in range(count - 1):
            if note_index >= note_count:
                break
            prev = notes[note_index - 1]
            cur = notes[note_index]
            gap = max(0.0, float(cur["start"]) - float(prev["end"]))
            if gap <= max_slur_gap_s:
                lyrics.append("+~")
            else:
                # A slur marker after a real pause is invalid in OpenUtau.
                # Re-articulate the same syllable instead of generating an
                # isolated '+~' phoneme.
                lyrics.append(unit)
            note_index += 1

    while len(lyrics) < note_count:
        lyrics.append(units[-1])
    return lyrics[:note_count]


def assign_lyrics(
    lines: list[str],
    notes: list[dict[str, Any]],
    ranges: list[tuple[int, int]],
    max_slur_gap_s: float,
) -> list[str]:
    result = ["a"] * len(notes)
    for line, (start, end) in zip(lines, ranges):
        if end <= start:
            continue
        units = singing_units_for_line(line)
        assigned = distribute_units(
            units,
            notes[start:end],
            max_slur_gap_s=max_slur_gap_s,
        )
        for offset, lyric in enumerate(assigned):
            result[start + offset] = lyric
    return result


def _make_ustx_note(note: dict[str, Any], lyric: str, ticks_per_second: float) -> dict[str, Any]:
    start = int(round(float(note["start"]) * ticks_per_second))
    end = int(round(float(note["end"]) * ticks_per_second))
    duration = max(10, end - start)
    return {
        "position": start,
        "duration": duration,
        "tone": int(note["midi"]),
        "lyric": lyric,
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


def _touch_slurs(ustx_notes: list[dict[str, Any]], max_gap_ticks: int) -> None:
    for i in range(1, len(ustx_notes)):
        cur = ustx_notes[i]
        prev = ustx_notes[i - 1]
        if not str(cur["lyric"]).startswith("+"):
            continue
        gap = cur["position"] - (prev["position"] + prev["duration"])
        if 0 <= gap <= max_gap_ticks:
            prev["duration"] += gap


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
    ranges = align_lines_to_notes(base_lines, source_notes, asr)
    slur_gap_s = float(cfg.get("slur_max_gap_ms", 120)) / 1000.0
    lyrics_by_note = assign_lyrics(
        base_lines, source_notes, ranges, max_slur_gap_s=slur_gap_s
    )

    repeat_plan: dict[str, Any] | None = None
    all_notes = [dict(n) for n in source_notes]
    all_lyrics = list(lyrics_by_note)

    if final_repeat is not None and previous_repeat_index is not None:
        # Convert section index to line index in the base line list.
        line_start = sum(len(s.lines) for s in base_sections[:previous_repeat_index])
        line_end = line_start + len(base_sections[previous_repeat_index].lines)
        if line_start < len(ranges) and line_end - 1 < len(ranges):
            src_note_start = ranges[line_start][0]
            src_note_end = ranges[line_end - 1][1]
            template = source_notes[src_note_start:src_note_end]

            if template:
                last_end = float(source_notes[-1]["end"])
                gap_s = float(cfg.get("repeat_section_gap_s", 0.45))
                dest_start = last_end + gap_s
                src_start = float(template[0]["start"])
                src_end = float(template[-1]["end"])
                shift = dest_start - src_start

                duplicated: list[dict[str, Any]] = []
                for n in template:
                    d = dict(n)
                    d["start"] = float(n["start"]) + shift
                    d["end"] = float(n["end"]) + shift
                    duplicated.append(d)

                final_ranges = align_lines_to_notes(final_repeat.lines, duplicated, None)
                final_lyrics = assign_lyrics(
                    final_repeat.lines,
                    duplicated,
                    final_ranges,
                    max_slur_gap_s=slur_gap_s,
                )
                all_notes.extend(duplicated)
                all_lyrics.extend(final_lyrics)

                repeat_plan = {
                    "enabled": True,
                    "section": final_repeat.label,
                    "source_start": src_start,
                    "source_end": src_end,
                    "dest_start": dest_start,
                    "dest_end": dest_start + (src_end - src_start),
                }
                log(
                    "[Structure] Appending final repeated chorus using the previous "
                    f"chorus melody ({src_start:.2f}-{src_end:.2f}s -> {dest_start:.2f}s)."
                )

    bpm = float(cfg.get("ustx_bpm", 120.0))
    ticks_per_second = 480.0 * bpm / 60.0
    ustx_notes = [
        _make_ustx_note(note, lyric, ticks_per_second)
        for note, lyric in zip(all_notes, all_lyrics)
    ]
    _touch_slurs(
        ustx_notes,
        max_gap_ticks=int(round(float(cfg.get("slur_max_gap_ms", 120)) / 1000.0 * ticks_per_second)),
    )

    end_tick = max(n["position"] + n["duration"] for n in ustx_notes) + 960
    project = {
        "name": "Vocal Translate AI - Ukrainian Guide",
        "comment": "Generated automatically. Source lyrics file is never modified.",
        "output_dir": "Vocal",
        "cache_dir": "UCache",
        "ustx_version": "0.10",
        "time_signatures": [
            {"bar_position": 0, "beat_per_bar": 4, "beat_unit": 4}
        ],
        "tempos": [
            {"position": 0, "bpm": bpm}
        ],
        "tracks": [
            {
                "singer": "",
                "phonemizer": "OpenUtau.Core.DiffSinger.DiffSingerUkrainianPhonemizer",
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
        "sections": [{"label": s.label, "lines": s.lines} for s in sections],
        "base_line_ranges": ranges,
        "repeat_plan": repeat_plan,
        "note_count": len(all_notes),
        "singing_unit_count": sum(
            len(singing_units_for_line(line))
            for sec in sections for line in sec.lines
        ),
    }
    (out_dir / "alignment.json").write_text(
        json.dumps(alignment, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log(f"[DiffSinger] USTX generated: {ustx_path}")
    return ustx_path, repeat_plan
