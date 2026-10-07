# Vocal Translate AI v6

> Быстрый старт: см. [QUICKSTART.md](QUICKSTART.md)

Коротко: положи **один или несколько MP3** в `input/`, добавь соответствующие TXT с переводом, запусти `RUN.bat`, забери готовые MP3 из `out/`.

Windows one-click local pipeline for translating a song vocal while preserving the original song structure.

## Pipeline

`input/original.mp3` -> Demucs -> delexicalized melody source -> ACE-Step SFT faithful cover -> Demucs vocal cleanup -> Seed-VC -> remix -> `out/original_uk.mp3`

The default flow:

1. Demucs `htdemucs` separates `bass.wav`, `drums.wav`, `other.wav`, and `vocals.wav`.
2. The three non-vocal stems are mixed into an instrumental.
3. A short target-voice reference is automatically created from the original vocal stem after trimming the leading silence.
4. ACE-Step 1.5 uses the **full original mix** as the Cover source, so melody, rhythm, chords, arrangement and section timing are better constrained.
5. The ACE result is separated again with Demucs `--two-stems vocals`; only the isolated generated vocal is passed to Seed-VC.
6. Seed-VC converts that clean guide vocal toward the reference timbre while preserving F0.
7. FFmpeg mixes the converted vocal with the original Demucs instrumental and writes a 320 kbps MP3 to `out/`.

## Input

Put one or more MP3 files into `input/`.

For a single track, `input/lyrics.txt` is supported. For batch mode, use matching files such as `input/lyrics/song1.txt` for `input/song1.mp3`, or place `song1.txt` beside `song1.mp3`. Section labels such as `[Verse 1]` and `[Chorus]` are supported. The pipeline adds ACE language metadata automatically; it does not rewrite the lyric text.

Optional: use `input/reference/<track name>.wav` for per-track voice references, or `input/reference.wav` as a shared fallback. Otherwise a reference is extracted automatically from each Demucs vocal stem.

Batch jobs are processed sequentially to limit VRAM usage. By default, one failed track does not stop the remaining tracks.

## Run

Double-click `RUN.bat`.

The setup check runs every time but does not reinstall healthy environments. On an already configured machine this is only a fast import/version check.

First-time setup needs Internet access for repositories, Python packages, and model weights. Later runs reuse local environments, package caches, and downloaded model weights.

## Quality changes in v6

- Strict Ukrainian mode no longer feeds the Russian singer reference into ACE-Step. Timbre transfer happens later in Seed-VC.
- The ACE cover source is now a delexicalized mix: instrumental + a heavily low-passed version of the original vocal. This keeps pitch/rhythm cues while suppressing most source-language consonant/formant information.
- On capable GPUs (including the RTX 5080 16 GB class) strict mode switches to `acestep-v15-sft` at 50 steps with CFG for better semantic/lyric parsing. Low-VRAM GPUs remain on Turbo.
- Ukrainian synthesis input gets Unicode-only cleanup (zero-width characters removed; standalone Latin `I` normalized to Cyrillic `І`). The user's TXT file itself is never rewritten.
- Seed-VC reference length is reduced to 8 seconds. Seed-VC has a 30-second context window; shorter reference leaves much more room for each source chunk and reduces crossfade/boundary artifacts.
- A diagnostic mix is written before Seed-VC as `*_uk_guide.mp3`. Compare it with the final `*_uk.mp3` to distinguish ACE pronunciation errors from voice-conversion artifacts.

- `audio_cover_strength=1.0` by default for a faithful cover.
- `cover_noise_strength=0.25` is now explicitly set. ACE-Step documents 0 as no melody retention; leaving it at the old default allowed excessive reinterpretation.
- ACE source defaults to the full original mix instead of the isolated vocal stem.
- ACE output is vocal-separated before Seed-VC to prevent instruments/noise from being converted into ghost vocals.
- Seed-VC uses 50 diffusion steps on normal GPUs for higher SVC quality; low-VRAM GPU profiles still reduce this automatically.
- FFmpeg limiters use `level=false`, so peak protection no longer auto-raises the backing track.
- ACE guide caching now includes a generation fingerprint. Old/bad guides are automatically invalidated when quality parameters change.

### Reliability retained from v4

- ACE-Step is started directly from its existing `.venv`; the pipeline does not use `uv run`, so normal runs cannot trigger an implicit environment sync.
- Existing ACE-Step and Seed-VC repositories are not automatically updated.
- Existing working environments are not rebuilt.
- ACE-Step first-time sync explicitly skips `flash-attn`; ACE-Step can use SDPA instead.
- Seed-VC installs only a minimal inference dependency set instead of its full GUI/training requirements.
- Seed-VC first tries the local uv cache before downloading the CUDA 12.8 PyTorch wheels.
- Demucs is installed into the ACE-Step Python environment, so it reuses the already installed CUDA PyTorch instead of creating another large PyTorch environment.
- Every stage has a persistent log, and `RUN.bat` always pauses after success or failure.

## Logs

- `logs/setup.log` - dependency/environment setup
- `logs/run.log` - end-to-end pipeline and traceback
- `logs/demucs.log` - stem separation
- `logs/ace_api.log` - ACE-Step API/model generation
- `logs/seed_vc.log` - Seed-VC inference/model downloads
- `logs/ffmpeg.log` - audio mixing/encoding
- `logs/launcher.log` - launcher exit status
- `logs/diagnose.log` - generated by `DIAGNOSE.bat`

If anything fails, run `DIAGNOSE.bat` and attach `logs/diagnose.log` plus the stage log named by the error.

## Repair

`SETUP_ONLY.bat` performs the normal non-destructive verification.

`FORCE_REPAIR.bat` intentionally re-runs environment installation checks. Use it only when an environment is actually corrupted.

## GPU profiles

The launcher detects the installed NVIDIA GPU and writes `runtime/gpu_profile.json`.

- **Modern profile** — RTX 50-series and other modern NVIDIA GPUs: PyTorch 2.7.1 + CUDA 12.8.
- **RTX 3080 profile** — GeForce RTX 3080 10/12 GB / Ampere `sm_86`: PyTorch 2.7.1 + CUDA 12.8, batch size 1, ACE VRAM released before Seed-VC, and ACE-Step VRAM tier forced from the card's actual 10/12 GB capacity.
- **GTX 970 profile** — GeForce GTX 970 4 GB / Maxwell `sm_52`: PyTorch 2.5.1 + CUDA 12.1, ACE-Step Tier-1, INT8/CPU offload, batch size 1, reduced Seed-VC steps, and explicit ACE shutdown before Seed-VC to release VRAM.
- Other <=4.5 GB GPUs are detected as low-VRAM and receive the memory-saving runtime profile, but the dedicated legacy PyTorch switch is currently specifically enabled for GTX 970.

Run `DIAGNOSE.bat` to see the detected GPU, compute capability, CUDA architecture list, and active profile.

## Repository hygiene

Audio, local lyrics, AI runtimes, downloaded models, generated work files, and logs are ignored by Git.

## Voice rights

For publishing or commercial use, make sure you have the required rights and permission for the song and any real person's voice likeness.

## Strict Ukrainian mode download note

On the first strict-quality run on a capable GPU, ACE-Step may download the SFT checkpoint if it is not already present. The SFT weight file is about 4.79 GB. This is a one-time model download; subsequent runs reuse it locally.
