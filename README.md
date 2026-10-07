# Vocal Translate AI v7

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

The default guide voice is **Nero v170**. Its timbre is only an intermediate guide; Seed-VC then transfers the source singer's timbre.

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
- Nero DiffSinger voicebank;
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
