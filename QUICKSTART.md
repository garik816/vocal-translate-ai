# Короткая инструкция v7

## 1. Что положить в `input`

Один трек:

```text
input/
  song.mp3
  lyrics.txt
```

Несколько треков:

```text
input/
  song1.mp3
  song2.mp3
  lyrics/
    song1.txt
    song2.txt
```

Имя TXT в batch-режиме должно совпадать с именем MP3.

Твой TXT — главный источник текста. Программа его не переписывает.

## 2. Запуск

```text
RUN.bat
```

Результаты:

```text
out/song_uk_guide.mp3
out/song_uk.mp3
```

- `*_uk_guide.mp3` — украинский DiffSinger **до Seed-VC**.
- `*_uk.mp3` — финал после переноса тембра.

Сначала слушай `*_uk_guide.mp3`. Если украинский там плохой, проблема ещё до Seed-VC.

## 3. Автоизвлечение текста из MP3

Можно отдельно запустить:

```text
EXTRACT_LYRICS.bat
```

Получишь:

```text
out/song_source_lyrics.txt
out/song_source_lyrics.srt
out/song_source_lyrics.json
```

Алгоритм:

```text
MP3 ID3 lyrics, если есть
        +
Demucs vocals -> Whisper -> текст + тайминги слов/фраз
```

Извлечённый исходный текст используется для тайминга и проверки. Он **не заменяет** твой украинский TXT.

## 4. Что делает v7

```text
MP3
 ↓
Demucs
 ↓
vocals ────────────────┐
 ↓                     │
Whisper -> тайминги    │
 ↓                     │
F0 -> ноты/мелодия     │
 ↓                     │
Ukrainian DiffSinger <-┘ + lyrics.txt
 ↓
украинский guide vocal
 ↓
Seed-VC
 ↓
оригинальный instrumental
 ↓
final MP3
```

ACE-Step в украинской ветке v7 больше не используется.

## 5. Поддержка GPU

- RTX 5080 / современные NVIDIA — полный режим.
- RTX 3080 10/12 GB — поддерживается.
- GTX 970 4 GB — отдельный low-VRAM профиль; Whisper работает на CPU, качество/шаги снижены автоматически.

## 6. Референс голоса

Опционально:

```text
input/reference.wav
```

или для batch:

```text
input/reference/song1.wav
input/reference/song2.wav
```

Если reference отсутствует, он автоматически берётся из оригинального vocal stem.

## 7. Дополнительный финальный припев

По умолчанию:

```json
"append_final_repeated_section": true
```

Если в твоём тексте есть дополнительный последний `[Chorus]`, v7 повторит мелодию и музыкальный участок предыдущего припева вместо попытки впихнуть текст в outro.

## 8. Если ошибка

Запусти:

```text
DIAGNOSE.bat
```

И пришли:

```text
logs/setup.log
logs/run.log
logs/asr.log
logs/melody.log
logs/diffsinger.log
logs/seed_vc.log
logs/ffmpeg.log
logs/diagnose.log
```

Первый запуск скачивает модели и voicebank. Последующие запуски используют локальный cache.


## 9. Voicebank для украинского

v7 теперь использует **Amaboshi Cipher v170** как промежуточный guide voice.

Почему не Nero: Nero загружался нормально, но не поддерживал набор украинских фонем, поэтому OpenUtau выдавал `valid=0, errors=...`.

Amaboshi Cipher нужен только для правильного украинского произношения и guide-вокала. Финальный тембр по-прежнему переносится Seed-VC с оригинального певца.

При первом запуске после обновления новый voicebank будет скачан один раз.
