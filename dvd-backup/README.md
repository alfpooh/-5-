# DVD Backup (macOS)

A one-time-run script that copies a video DVD to your Mac and shows a live progress bar with percent, speed and ETA.

- It copies the whole `VIDEO_TS` folder: the movie, **every audio language**, subtitles and menus.
- It retries scratched sectors. Sectors that still can't be read are zero-filled, so the copy finishes instead of aborting.
- With `--mkv`, it also makes a single `.mkv` file of the main feature. The file keeps all audio tracks and subtitles, with their language tags. Nothing is re-encoded, so there's no quality loss. This option needs `ffmpeg`.

## Run

### One compressed movie file (.mp4) — 한 개의 압축 파일

```bash
brew install ffmpeg                  # one time only
python3 dvd_backup.py --mp4          # -> ~/Movies/<disc name>.mp4
python3 dvd_backup.py --mp4 --fast   # Mac hardware encoder: much faster, somewhat bigger file
```

This makes **one** `.mp4` file with H.264 video and every audio language as a separate AAC track, each with its language tag. It reads straight from the disc, so there's no folder copy first. It usually comes out at 1–2 GB instead of 4–8 GB. It plays in QuickTime: switch languages with **View > Languages**. DVD subtitles are image-based and can't go into an MP4, so they're left out. Use `--mkv` if you need subtitles.

### Full disc copy

```bash
# 1. Insert the DVD and wait until it shows up in Finder
# 2. Run:
python3 dvd_backup.py                # -> ~/Movies/<disc name>/VIDEO_TS
python3 dvd_backup.py --mkv --eject  # also build <disc name>.mkv, then eject
```

Options: `--dest <folder>`, `--name <name>`, `--source /Volumes/<DISC>`, `--mp4`, `--fast`, `--mkv`, `--eject`.

For `--mkv`, install ffmpeg first: `brew install ffmpeg`.
If `python3` isn't installed, run `xcode-select --install`.

## Play

- **VIDEO_TS folder:** open it in [VLC](https://www.videolan.org/) or [IINA](https://iina.io/). You get the full DVD menus and can switch the audio language.
- **.mkv file:** open it in VLC or IINA, and pick the language from the audio track menu. QuickTime can't play MKV.

## Note

The script copies the disc as macOS presents it. It does not remove copy protection (CSS). A backup of a CSS-protected commercial disc won't play. Home-made and unprotected discs work fine.
