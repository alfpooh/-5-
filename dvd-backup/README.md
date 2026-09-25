# DVD Backup (macOS)

A one-time-run script that copies a video DVD to your Mac and shows a live progress bar with percent, speed and ETA.

- It copies the whole `VIDEO_TS` folder: the movie, **every audio language**, subtitles and menus.
- It retries scratched sectors. Sectors that still can't be read are zero-filled, so the copy finishes instead of aborting.
- With `--mkv`, it also makes a single `.mkv` file of the main feature. The file keeps all audio tracks and subtitles, with their language tags. Nothing is re-encoded, so there's no quality loss. This option needs `ffmpeg`.

## Run

```bash
# 1. Insert the DVD and wait until it shows up in Finder
# 2. Run:
python3 dvd_backup.py                # -> ~/Movies/<disc name>/VIDEO_TS
python3 dvd_backup.py --mkv --eject  # also build <disc name>.mkv, then eject
```

Options: `--dest <folder>`, `--name <name>`, `--source /Volumes/<DISC>`, `--mkv`, `--eject`.

For `--mkv`, install ffmpeg first: `brew install ffmpeg`.
If `python3` isn't installed, run `xcode-select --install`.

## Play

- **VIDEO_TS folder:** open it in [VLC](https://www.videolan.org/) or [IINA](https://iina.io/). You get the full DVD menus and can switch the audio language.
- **.mkv file:** open it in VLC or IINA, and pick the language from the audio track menu. QuickTime can't play MKV.

## Note

The script copies the disc as macOS presents it. It does not remove copy protection (CSS). A backup of a CSS-protected commercial disc won't play. Home-made and unprotected discs work fine.
