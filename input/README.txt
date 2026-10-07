INPUT FOLDER - Vocal Translate AI v7

SINGLE TRACK:
  song.mp3
  lyrics.txt

BATCH:
  song1.mp3
  song2.mp3
  lyrics\song1.txt
  lyrics\song2.txt

You can also place matching lyric files beside the MP3:
  song1.mp3
  song1.txt

Optional per-track voice references:
  reference\song1.wav
  reference\song2.wav

Fallback shared reference:
  reference.wav

If no reference WAV exists, the program automatically creates one
from each track's Demucs vocal stem.

SOURCE LYRICS EXTRACTION:
  Run EXTRACT_LYRICS.bat

Outputs:
  out\song_source_lyrics.txt
  out\song_source_lyrics.srt
  out\song_source_lyrics.json

The extracted source lyrics are used for timing/alignment support.
They never replace your target Ukrainian TXT automatically.

V7 OUTPUT:
  out\song_uk_guide.mp3   - Ukrainian DiffSinger before Seed-VC
  out\song_uk.mp3         - final voice-converted mix

Important:
- One or many MP3 files are supported.
- In batch mode, lyric filenames must match MP3 filenames.
- Processing is sequential to avoid exhausting GPU VRAM.
- Audio, lyrics, runtime models and generated data are ignored by Git.
