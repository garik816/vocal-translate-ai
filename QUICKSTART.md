# Короткая инструкция

## Что положить в `input`

### Один трек

```text
input/
  song.mp3
  lyrics.txt
```

### Несколько треков

Рекомендуемый вариант:

```text
input/
  song1.mp3
  song2.mp3
  lyrics/
    song1.txt
    song2.txt
```

Также поддерживается:

```text
input/
  song1.mp3
  song1.txt
  song2.mp3
  song2.txt
```

Имя TXT должно совпадать с именем MP3.

Пример:

```text
Крематорий - Мусорный ветер.mp3
lyrics/Крематорий - Мусорный ветер.txt
```

## Референс голоса

Не обязателен. Если его нет, программа сама вырежет референс из вокала после Demucs.

Для отдельного референса на каждый трек:

```text
input/
  reference/
    song1.wav
    song2.wav
```

Также можно использовать общий:

```text
input/reference.wav
```

Приоритет:

1. `input/reference/<имя трека>.wav`
2. `input/<имя трека>.wav`
3. `input/reference.wav`
4. автоматический референс из Demucs

## Как запустить

Запустить:

```text
RUN.bat
```

Все MP3 обрабатываются **последовательно**, чтобы не переполнять VRAM.

Результаты:

```text
out/
  song1_uk.mp3
  song2_uk.mp3
```

Если ACE-Step вернёт несколько вариантов:

```text
song1_uk_01.mp3
song1_uk_02.mp3
```

## Что делает автоматически

```text
MP3
 ↓
Demucs
 ↓
vocals / drums / bass / other
 ↓
ACE-Step 1.5
 ↓
Seed-VC
 ↓
FFmpeg
 ↓
готовый MP3
```

Текст из TXT программа **не переписывает**.

## Что поддерживается

- Windows
- NVIDIA CUDA
- RTX 50-series / CUDA 12.8
- один или несколько MP3
- вход: MP3
- текст: UTF-8 TXT
- референс: WAV
- выход: MP3 320 kbps
- Demucs `htdemucs`
- ACE-Step 1.5
- Seed-VC
- метки `[Verse]`, `[Verse 1]`, `[Chorus]`
- язык нового вокала задаётся через `vocal_language` в `config.json`

## Batch-настройки

В `config.json`:

- `batch_stop_on_error: false` — если один трек упал, продолжать остальные.
- `batch_shared_lyrics: false` — в batch по умолчанию нужен отдельный TXT для каждого MP3.
- Если поставить `batch_shared_lyrics: true`, один `input/lyrics.txt` будет использоваться для всех MP3.

## Если ошибка

Запустить:

```text
DIAGNOSE.bat
```

Логи:

```text
logs/diagnose.log
logs/run.log
logs/setup.log
logs/demucs.log
logs/ace_api.log
logs/seed_vc.log
logs/ffmpeg.log
```

`SETUP_ONLY.bat` — проверка окружения.

`FORCE_REPAIR.bat` — принудительный ремонт зависимостей.

## Первый запуск

Первый запуск требует Интернет для скачивания репозиториев, Python-пакетов и моделей. После установки основная работа выполняется локально.
