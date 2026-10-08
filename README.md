# Vocal Translate AI v7.5.0

Windows one-click pipeline for translating sung vocals into Ukrainian while preserving the original melody and timing.

> Quick start: see [QUICKSTART.md](QUICKSTART.md).

## v7 pipeline

```text
input/song.mp3
input/lyrics.txt
        |
        v
Demucs -> vocals + instrumental
        |
        +-> ID3/Whisper -> source lyrics + word/segment timings
        |
        +-> F0 extraction -> melody / note events
        |
        v
OpenUtau-Lunai + Ukrainian DiffSinger phonemizer
        |
        v
clean Ukrainian guide vocal
        |
        +-> out/song_uk_guide.mp3
        |
        v
Seed-VC -> source-singer timbre transfer
        |
        v
FFmpeg -> out/song_uk.mp3
```

ACE-Step is no longer used in the v7 Ukrainian synthesis path.

## Input

Single track:

```text
input/
  song.mp3
  lyrics.txt
```

Batch mode:

```text
input/
  song1.mp3
  song2.mp3
  lyrics/
    song1.txt
    song2.txt
```

The target TXT is treated as authoritative. The program does not rewrite it. Invisible Unicode characters are only normalized internally when needed by synthesis.

Optional voice reference:

```text
input/reference.wav
input/reference/song1.wav
```

If no reference is supplied, one is extracted automatically from the original vocal stem.

## Source-text extraction

v7 can extract the original-language lyrics from the MP3.

Run:

```text
EXTRACT_LYRICS.bat
```

The extractor checks embedded MP3 lyrics first and also runs Whisper on the Demucs vocal stem to obtain timing information.

Outputs:

```text
out/song_source_lyrics.txt
out/song_source_lyrics.srt
out/song_source_lyrics.json
```

The extracted source text is used for alignment/timing support. It never silently replaces the target Ukrainian lyrics.

## Ukrainian synthesis

v7 uses OpenUtau-Lunai because it contains a native `DiffSingerUkrainianPhonemizer`.

The default guide voice is **Amaboshi Cipher v170**, because this voicebank explicitly supports Ukrainian. Its timbre is only an intermediate guide; Seed-VC then transfers the source singer's timbre.

Generated diagnostics:

```text
work/<song>/source_lyrics/
work/<song>/melody/melody.json
work/<song>/diffsinger/guide.ustx
work/<song>/diffsinger/alignment.json
work/<song>/diffsinger/guide_uk.wav
```

The most important output for quality checking is:

```text
out/song_uk_guide.mp3
```

If this file already has bad Ukrainian pronunciation, the issue is in alignment/DiffSinger and Seed-VC should not be blamed. If the guide is clean but `song_uk.mp3` is damaged, the problem is in Seed-VC.

## Final repeated chorus

When the target lyrics contain an extra final chorus that is not present in the source structure, v7 can duplicate the previous chorus melody and instrumental section automatically:

```json
"append_final_repeated_section": true
```

The user's lyric text is still preserved.

## GPU profiles

Supported profiles:

- **RTX 5080 / modern NVIDIA** — CUDA 12.8 PyTorch, Whisper on CUDA, DiffSinger via DirectML, full Seed-VC quality.
- **RTX 3080 10/12 GB** — CUDA 12.8 PyTorch, CUDA Whisper, DirectML DiffSinger, slightly reduced Seed-VC load.
- **GTX 970 4 GB** — legacy PyTorch CUDA 12.1 / `sm_52`; Whisper forced to CPU; lighter DiffSinger and Seed-VC settings.

Demucs automatically falls back to CPU if its CUDA pass fails.

## First run / downloads

The first run needs Internet for:

- Seed-VC repository and model weights;
- Demucs model;
- Whisper model;
- OpenUtau-Lunai source/build dependencies;
- Amaboshi Cipher DiffSinger voicebank;
- Python/.NET dependencies.

After these assets are cached locally, normal synthesis is local.

## Logs

```text
logs/setup.log
logs/run.log
logs/demucs.log
logs/asr.log
logs/melody.log
logs/diffsinger.log
logs/seed_vc.log
logs/ffmpeg.log
logs/diagnose.log
```

Run `DIAGNOSE.bat` if something fails.

## Repair

- `SETUP_ONLY.bat` — verify/install missing dependencies.
- `FORCE_REPAIR.bat` — force environment repair.
- `EXTRACT_LYRICS.bat` — only source-text extraction.

## Repository hygiene

Audio, lyrics, generated output, downloaded models, runtime environments and logs are ignored by Git.

## Rights

For publication or commercial use, make sure you have the necessary rights for the song and any real person's voice likeness.


## Why Nero was replaced

Early v7 builds used Nero v170. OpenUtau loaded Nero correctly, but the Ukrainian phonemizer produced phonemes that Nero could not map: the whole phrase validated as errors. Amaboshi Cipher v170 is used instead because its multilingual DiffSinger release includes Ukrainian support.


## v7.2 alignment change

v7.2 no longer uses every extracted F0 segment as a lyric note.

The timing pipeline is now:

```text
Whisper word timestamps -> target lyric lines -> Ukrainian syllables
                                             |
                                             v
                                   one clean lyric note per syllable

F0 extraction --------------------------------^
                     pitch only; never decides where lyrics begin
```

This prevents instrument leakage in a Demucs vocal stem from creating fake lyric notes during intros/interludes. Instrumental gaps remain instrumental. Pitch extraction is now used only to choose the pitch of each ASR-anchored syllable note.


## v7.3 pronunciation fix

v7.2 still split Ukrainian target words into orthographic syllable fragments before handing them to the Ukrainian G2P. That made DiffSinger pronounce fragments as standalone lexical items and caused the broken/chopped diction heard in the guide.

v7.3 changes the synthesis unit to a **whole Ukrainian word**:

```text
Whisper source-word timing -> target line -> target whole words
F0                         -> pitch only
```

Each target word is sent intact to the Ukrainian phonemizer. Section timing is also solved independently for Verse/Chorus blocks using the strongest pauses in the original vocal, so a chorus cannot expand across an interlude or into the next verse.


## v7.3.1 section-alignment fix

v7.3 used the globally largest ASR pauses as section boundaries. On the current source track, Whisper hallucinated a short five-word island inside the long instrumental break; this created an impossible section with only five source words for six target lyric lines.

v7.3.1:
- removes tiny isolated ASR islands inside long instrumental gaps;
- partitions Verse/Chorus sections with dynamic programming using target syllable weight and source pauses;
- enforces enough source words for every target line;
- detects when chorus 2 and the extra final chorus were written under one heading, splits them, and uses chorus 2 as the repeat template.


## v7.4 pronunciation / melody refinement

v7.3 fixed G2P by passing complete Ukrainian words, but it still placed an entire multi-syllable word on one pitch note. That made diction recognizable but robotic and blurred word stress.

v7.4 keeps the whole word as one OpenUtau phonemizer group while giving each syllable its own note:

```text
сміттєвий   +   +
   |        |   |
 syllable1  2   3
```

Only the first note contains the complete Ukrainian word; continuation notes use OpenUtau `+`, so Ukrainian G2P receives the intact lexical word exactly once and DiffSinger distributes its phonemes across the syllable notes.

F0 is sampled separately for each syllable note, with conservative octave-error folding to reduce obvious pitch-tracker jumps.


## v7.5 melody-aware syllable groups

v7.4 correctly kept the whole Ukrainian word in one G2P group, but each target syllable still occupied one very large note. This produced exaggerated sustained vowels and smeared consonants.

v7.5 uses OpenUtau's intended hierarchy:

```text
whole word -> lexical syllables -> pitch/melisma notes
```

For a multi-syllable word:
- the first lexical syllable carries the complete Ukrainian word;
- later lexical syllables use `+`;
- extra source pitch changes inside one syllable use `+~`;
- source F0 notes are clipped/merged inside each syllable timing cell instead of stretching one pitch across the whole syllable;
- obvious octave-tracker jumps are folded conservatively.

This keeps Ukrainian G2P lexical context while following the original sung melody more closely.
