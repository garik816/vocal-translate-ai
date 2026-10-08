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


def _remove_isolated_asr_islands(
    words: list[dict[str, Any]],
    min_gap_s: float = 2.0,
    long_gap_s: float = 8.0,
    max_words: int = 5,
    max_duration_s: float = 4.0,
) -> list[dict[str, Any]]:
    """Drop tiny ASR hallucination islands inside long instrumental breaks."""
    if len(words) < 3:
        return words

    # Split into clusters at meaningful pauses.
    clusters: list[tuple[int, int]] = []
    start = 0
    for i in range(1, len(words)):
        gap = float(words[i]["start"]) - float(words[i - 1]["end"])
        if gap >= min_gap_s:
            clusters.append((start, i))
            start = i
    clusters.append((start, len(words)))

    drop_ranges: list[tuple[int, int]] = []
    for ci, (a, b) in enumerate(clusters):
        count = b - a
        duration = float(words[b - 1]["end"]) - float(words[a]["start"])
        prev_gap = (
            float(words[a]["start"]) - float(words[a - 1]["end"])
            if a > 0 else 0.0
        )
        next_gap = (
            float(words[b]["start"]) - float(words[b - 1]["end"])
            if b < len(words) else 0.0
        )

        # Typical failure: Whisper hallucinates 1-5 words from instrumental
        # bleed in the middle of a long interlude.
        surrounded = (
            (prev_gap >= min_gap_s and next_gap >= long_gap_s)
            or (next_gap >= min_gap_s and prev_gap >= long_gap_s)
        )
        if (
            count <= max_words
            and duration <= max_duration_s
            and surrounded
        ):
            drop_ranges.append((a, b))
            log(
                "[Alignment] Dropping isolated ASR island: "
                f"{words[a]['start']:.2f}-{words[b - 1]['end']:.2f}s, "
                f"words={count}, gaps={prev_gap:.2f}/{next_gap:.2f}s."
            )

    if not drop_ranges:
        return words

    keep: list[dict[str, Any]] = []
    for i, word in enumerate(words):
        if any(a <= i < b for a, b in drop_ranges):
            continue
        keep.append(word)
    return keep


def _partition_words_into_sections(
    words: list[dict[str, Any]],
    sections: list[LyricSection],
) -> list[list[dict[str, Any]]]:
    """DP partition using section lyric weight plus real vocal pauses.

    Unlike the old implementation, this cannot create a section with fewer
    source words than target lines, and it does not blindly choose the N
    largest gaps.
    """
    n_sections = len(sections)
    if n_sections <= 1:
        return [words]
    if not words:
        raise RuntimeError("No ASR words available for section alignment.")

    min_words = [max(1, len(sec.lines)) for sec in sections]
    if len(words) < sum(min_words):
        raise RuntimeError(
            f"ASR produced {len(words)} words, but at least "
            f"{sum(min_words)} are required for {n_sections} lyric sections."
        )

    target_weights = [
        max(
            1,
            sum(
                target_syllables(word)
                for line in sec.lines
                for word in words_for_line(line)
            ),
        )
        for sec in sections
    ]

    prefix_syll = [0]
    for word in words:
        prefix_syll.append(prefix_syll[-1] + int(word["syllables"]))

    total_source = float(prefix_syll[-1])
    total_target = float(sum(target_weights))
    expected_syll = [
        max(1.0, total_source * weight / total_target)
        for weight in target_weights
    ]

    # Prefix/suffix minimum word counts make the DP search valid by design.
    prefix_min = [0]
    for value in min_words:
        prefix_min.append(prefix_min[-1] + value)
    suffix_min = [0] * (n_sections + 1)
    for i in range(n_sections - 1, -1, -1):
        suffix_min[i] = suffix_min[i + 1] + min_words[i]

    n_words = len(words)
    inf = 1e30
    dp = [[inf] * (n_words + 1) for _ in range(n_sections + 1)]
    prev = [[-1] * (n_words + 1) for _ in range(n_sections + 1)]
    dp[0][0] = 0.0

    for si in range(1, n_sections + 1):
        min_end = prefix_min[si]
        max_end = n_words - suffix_min[si]
        exp = expected_syll[si - 1]

        for end in range(min_end, max_end + 1):
            start_min = prefix_min[si - 1]
            start_max = end - min_words[si - 1]

            # Bound pathological spans while still allowing generous variance.
            max_span = max(
                min_words[si - 1] + 8,
                int(math.ceil(exp * 2.3 + 8)),
            )
            start_min = max(start_min, end - max_span)

            for start in range(start_min, start_max + 1):
                if dp[si - 1][start] >= inf:
                    continue

                src_syll = prefix_syll[end] - prefix_syll[start]
                mismatch = ((src_syll - exp) ** 2) / max(1.0, exp)

                # Strongly prefer boundaries at real pauses, but syllable
                # balance still matters enough to reject tiny fake sections.
                if end < n_words:
                    gap = max(
                        0.0,
                        float(words[end]["start"])
                        - float(words[end - 1]["end"]),
                    )
                    boundary_penalty = 4.0 * math.exp(-gap / 0.28)
                else:
                    boundary_penalty = 0.0

                duration = (
                    float(words[end - 1]["end"])
                    - float(words[start]["start"])
                )
                expected_duration = max(4.0, exp * 0.55)
                duration_penalty = (
                    max(0.0, duration - expected_duration * 2.3) ** 2
                    * 0.025
                )

                cost = (
                    dp[si - 1][start]
                    + mismatch
                    + boundary_penalty
                    + duration_penalty
                )
                if cost < dp[si][end]:
                    dp[si][end] = cost
                    prev[si][end] = start

    if prev[n_sections][n_words] < 0:
        raise RuntimeError(
            "Could not find a valid section partition from ASR timings."
        )

    ranges: list[tuple[int, int]] = []
    end = n_words
    for si in range(n_sections, 0, -1):
        start = prev[si][end]
        ranges.append((start, end))
        end = start
    ranges.reverse()

    regions = [words[a:b] for a, b in ranges]
    log(
        "[Alignment] DP section windows: "
        + " | ".join(
            f"{sections[i].label}:"
            f"{region[0]['start']:.2f}-{region[-1]['end']:.2f}s/"
            f"{len(region)}w"
            for i, region in enumerate(regions)
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


def _stabilize_tone(
    raw_tone: int,
    previous_tone: int,
) -> int:
    """Fold obvious octave-tracking errors without flattening real melody."""
    raw_tone = int(max(24, min(96, raw_tone)))
    previous_tone = int(max(24, min(96, previous_tone)))

    if abs(raw_tone - previous_tone) < 9:
        return raw_tone

    candidates = [
        tone
        for tone in (raw_tone - 12, raw_tone, raw_tone + 12)
        if 36 <= tone <= 84
    ]
    if not candidates:
        return raw_tone

    best = min(candidates, key=lambda tone: abs(tone - previous_tone))
    if abs(best - previous_tone) + 3 < abs(raw_tone - previous_tone):
        return best
    return raw_tone


def _allocate_cells(
    weights: list[int],
    total_cells: int,
) -> list[int] | None:
    """Allocate contiguous source syllable cells with at least one per item."""
    n = len(weights)
    if n == 0:
        return []
    if total_cells < n:
        return None

    counts = [1] * n
    remaining = total_cells - n
    if remaining <= 0:
        return counts

    safe_weights = [max(1, int(w)) for w in weights]
    total_weight = float(sum(safe_weights))
    raw = [remaining * w / total_weight for w in safe_weights]
    floors = [int(math.floor(x)) for x in raw]
    for i, value in enumerate(floors):
        counts[i] += value

    left = remaining - sum(floors)
    order = sorted(
        range(n),
        key=lambda i: raw[i] - floors[i],
        reverse=True,
    )
    for i in order[:left]:
        counts[i] += 1
    return counts


def _target_word_syllable_intervals(
    line: str,
    timing: LineTiming,
) -> list[tuple[str, list[tuple[float, float]]]]:
    """Map target words/syllables onto whole source syllable timing cells."""
    target_words = words_for_line(line)
    if not target_words:
        return []

    cells = _source_syllable_cells(timing.words)
    if not cells:
        return []

    word_weights = [target_syllables(word) for word in target_words]
    word_cell_counts = _allocate_cells(word_weights, len(cells))

    # Rare fallback when Whisper supplies fewer source syllable cells than
    # target words. Preserve line edges but still keep each target word local.
    if word_cell_counts is None:
        out: list[tuple[str, list[tuple[float, float]]]] = []
        total_weight = float(sum(word_weights))
        acc = 0.0
        for word, weight in zip(target_words, word_weights):
            word_start = timing.start + (
                (timing.end - timing.start) * acc / total_weight
            )
            acc += weight
            word_end = timing.start + (
                (timing.end - timing.start) * acc / total_weight
            )
            syllable_count = max(1, target_syllables(word))
            syllables = []
            for si in range(syllable_count):
                a = word_start + (word_end - word_start) * si / syllable_count
                b = word_start + (word_end - word_start) * (si + 1) / syllable_count
                syllables.append((a, max(a + 0.08, b)))
            out.append((word, syllables))
        return out

    out: list[tuple[str, list[tuple[float, float]]]] = []
    cell_pos = 0
    for word, word_cell_count in zip(target_words, word_cell_counts):
        word_cells = cells[cell_pos:cell_pos + word_cell_count]
        cell_pos += word_cell_count

        syllable_count = max(1, target_syllables(word))
        syllable_cell_counts = _allocate_cells(
            [1] * syllable_count,
            len(word_cells),
        )

        if syllable_cell_counts is None:
            word_start = float(word_cells[0][0])
            word_end = float(word_cells[-1][1])
            syllables = [
                (
                    word_start + (word_end - word_start) * si / syllable_count,
                    word_start + (word_end - word_start) * (si + 1) / syllable_count,
                )
                for si in range(syllable_count)
            ]
            out.append((word, syllables))
            continue

        cell_groups: list[list[tuple[float, float]]] = []
        pos = 0
        for count in syllable_cell_counts:
            cell_groups.append(word_cells[pos:pos + count])
            pos += count

        # Keep the word continuous for OpenUtau grouping, but put syllable
        # boundaries halfway across any source inter-cell gap.
        boundaries: list[float] = [float(word_cells[0][0])]
        for left, right in zip(cell_groups, cell_groups[1:]):
            left_end = float(left[-1][1])
            right_start = float(right[0][0])
            boundaries.append((left_end + right_start) * 0.5)
        boundaries.append(float(word_cells[-1][1]))

        syllables: list[tuple[float, float]] = []
        for si in range(syllable_count):
            a = boundaries[si]
            b = boundaries[si + 1]
            syllables.append((a, max(a + 0.08, b)))
        out.append((word, syllables))

    return out


def _merge_pitch_events(
    events: list[dict[str, float]],
    max_events: int,
    min_event_s: float,
) -> list[dict[str, float]]:
    events = [dict(e) for e in events]
    if not events:
        return events

    def merge_pair(i: int) -> None:
        left = events[i]
        right = events[i + 1]
        dl = max(0.001, left["end"] - left["start"])
        dr = max(0.001, right["end"] - right["start"])
        tone = (left["tone"] * dl + right["tone"] * dr) / (dl + dr)
        events[i] = {
            "start": left["start"],
            "end": right["end"],
            "tone": tone,
        }
        del events[i + 1]

    while len(events) > 1:
        too_short = [
            i
            for i, event in enumerate(events)
            if event["end"] - event["start"] < min_event_s
        ]
        if too_short:
            i = too_short[0]
            if i == 0:
                merge_pair(0)
            elif i == len(events) - 1:
                merge_pair(i - 1)
            else:
                left_diff = abs(events[i]["tone"] - events[i - 1]["tone"])
                right_diff = abs(events[i]["tone"] - events[i + 1]["tone"])
                merge_pair(i - 1 if left_diff <= right_diff else i)
            continue

        if len(events) <= max_events:
            break

        candidates = []
        for i in range(len(events) - 1):
            pitch_diff = abs(events[i]["tone"] - events[i + 1]["tone"])
            duration = (
                events[i]["end"] - events[i]["start"]
                + events[i + 1]["end"] - events[i + 1]["start"]
            )
            candidates.append((pitch_diff, duration, i))
        _, _, idx = min(candidates)
        merge_pair(idx)

    return events


def _pitch_events_for_syllable(
    start: float,
    end: float,
    source_notes: list[dict[str, Any]],
    previous_tone: int,
    max_events: int,
    min_event_s: float,
) -> list[dict[str, float]]:
    overlaps: list[dict[str, float]] = []
    for note in source_notes:
        ns = float(note["start"])
        ne = float(note["end"])
        a = max(start, ns)
        b = min(end, ne)
        if b - a < 0.025:
            continue
        overlaps.append({
            "start": a,
            "end": b,
            "tone": float(note.get("midi_float", note["midi"])),
        })

    if not overlaps:
        tone = _tone_for_interval(
            start,
            end,
            source_notes,
            fallback=previous_tone,
        )
        return [{
            "start": start,
            "end": end,
            "tone": float(tone),
        }]

    overlaps.sort(key=lambda e: (e["start"], e["end"]))

    # Merge nearly identical adjacent F0 events before constructing a
    # continuous syllable envelope.
    compact: list[dict[str, float]] = []
    for event in overlaps:
        if (
            compact
            and abs(compact[-1]["tone"] - event["tone"]) <= 0.75
            and event["start"] - compact[-1]["end"] <= 0.10
        ):
            compact[-1]["end"] = max(compact[-1]["end"], event["end"])
        else:
            compact.append(dict(event))

    # Convert voiced F0 events into one continuous word-internal melody
    # envelope. This is deliberate: OpenUtau continuation notes must touch
    # exactly or they stop belonging to the same lexical word.
    boundaries = [start]
    for left, right in zip(compact, compact[1:]):
        boundaries.append(
            max(
                boundaries[-1] + 0.02,
                min(
                    end,
                    (float(left["end"]) + float(right["start"])) * 0.5,
                ),
            )
        )
    boundaries.append(end)

    continuous: list[dict[str, float]] = []
    for i, event in enumerate(compact):
        a = boundaries[i]
        b = boundaries[i + 1]
        if b <= a:
            continue
        continuous.append({
            "start": a,
            "end": b,
            "tone": event["tone"],
        })

    continuous = _merge_pitch_events(
        continuous,
        max_events=max(1, max_events),
        min_event_s=max(0.04, min_event_s),
    )

    # Re-assert exact syllable edges after merging.
    continuous[0]["start"] = start
    continuous[-1]["end"] = end
    return continuous


def _build_word_notes(
    line: str,
    timing: LineTiming,
    source_notes: list[dict[str, Any]],
    cfg: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cfg = cfg or {}
    word_map = _target_word_syllable_intervals(line, timing)
    out: list[dict[str, Any]] = []
    previous_tone = 60

    max_melisma = int(cfg.get("max_melisma_notes_per_syllable", 3))
    min_pitch_event_s = float(cfg.get("min_pitch_event_ms", 90)) / 1000.0

    for word, syllables in word_map:
        for syllable_index, (start, end) in enumerate(syllables):
            events = _pitch_events_for_syllable(
                start,
                end,
                source_notes,
                previous_tone=previous_tone,
                max_events=max_melisma,
                min_event_s=min_pitch_event_s,
            )

            for event_index, event in enumerate(events):
                raw_tone = int(round(event["tone"]))
                tone = _stabilize_tone(raw_tone, previous_tone)
                previous_tone = tone

                if syllable_index == 0 and event_index == 0:
                    lyric = word
                elif event_index == 0:
                    # Next lexical syllable in the same whole-word G2P group.
                    lyric = "+"
                else:
                    # Extra pitch movement inside the current vowel/melisma.
                    lyric = "+~"

                out.append({
                    "start": float(event["start"]),
                    "end": float(event["end"]),
                    "midi": tone,
                    "midi_float": float(tone),
                    "lyric": lyric,
                    "word": word,
                    "syllable_index": syllable_index,
                    "melisma_index": event_index,
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


def _split_final_repeat(
    sections: list[LyricSection],
    enabled: bool,
) -> tuple[list[LyricSection], LyricSection | None, int | None]:
    if not enabled or len(sections) < 2:
        return sections, None, None

    last = sections[-1]
    if section_key(last.label) != "chorus":
        return sections, None, None

    prev_idx = None
    for idx in range(len(sections) - 2, -1, -1):
        if section_key(sections[idx].label) == "chorus":
            prev_idx = idx
            break
    if prev_idx is None:
        return sections, None, None

    previous = sections[prev_idx]
    template_len = len(previous.lines)
    if template_len <= 0:
        return sections, None, None

    # Case A: final repeat already has its own [Chorus] block.
    if len(last.lines) == template_len:
        return sections[:-1], last, prev_idx

    # Case B: the second chorus and the extra final chorus were written under
    # one heading (12 lines for two 6-line choruses in the current song).
    if len(last.lines) > template_len:
        prefix = last.lines[:-template_len]
        tail = last.lines[-template_len:]
        if prefix:
            base = sections[:-1] + [
                LyricSection(label=last.label, lines=prefix)
            ]
            repeat = LyricSection(
                label=f"{last.label} (final repeat)",
                lines=tail,
            )
            template_idx = len(base) - 1
            log(
                "[Structure] Split combined final chorus: "
                f"base_lines={len(prefix)}, repeat_lines={len(tail)}."
            )
            return base, repeat, template_idx

    return sections, None, None


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
    base_sections, final_repeat, previous_repeat_index = _split_final_repeat(
        sections,
        append_repeat,
    )

    source_words = _trim_trailing_outro(
        _asr_words(asr),
        float(cfg.get("asr_outro_gap_s", 4.0)),
    )
    source_words = _remove_isolated_asr_islands(
        source_words,
        min_gap_s=float(cfg.get("asr_island_min_gap_s", 2.0)),
        long_gap_s=float(cfg.get("asr_island_long_gap_s", 8.0)),
        max_words=int(cfg.get("asr_island_max_words", 5)),
        max_duration_s=float(cfg.get("asr_island_max_duration_s", 4.0)),
    )
    if not source_words:
        raise RuntimeError("No usable Whisper word timings for v7.3 alignment.")

    source_regions = _partition_words_into_sections(
        source_words,
        base_sections,
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
            notes = _build_word_notes(line, timing, source_notes, cfg)
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
                "generated_syllable_notes": len(notes),
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
                notes = _build_word_notes(line, timing, shifted_source_notes, cfg)
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
        raise RuntimeError("v7.5 generated no target syllable notes.")

    end_tick = max(
        note["position"] + note["duration"]
        for note in ustx_notes
    ) + 960

    project = {
        "name": "Vocal Translate AI - Ukrainian Guide",
        "comment": (
            "v7.5: whole-word Ukrainian G2P with syllable-note groups, section-aware ASR timing. "
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
        "mode": "v7.5_word_syllable_melisma",
        "sections": debug_sections,
        "repeat_plan": repeat_plan,
        "source_f0_note_count": len(source_notes),
        "generated_target_singing_notes": len(ustx_notes),
    }
    (out_dir / "alignment.json").write_text(
        json.dumps(alignment, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log(
        "[DiffSinger] v7.5 word/syllable/melisma USTX generated: "
        f"{len(ustx_notes)} syllable notes -> {ustx_path}"
    )
    return ustx_path, repeat_plan
