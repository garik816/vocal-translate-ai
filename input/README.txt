INPUT FOLDER

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

Important:
- One or many MP3 files are supported.
- In batch mode, lyric filenames must match MP3 filenames.
- Processing is sequential to avoid exhausting GPU VRAM.
- Audio, lyrics and generated data are ignored by Git.
