#!/usr/bin/env python3
"""
DVD backup for macOS — one-shot, with a live progress bar.

What it does
  1. Finds the DVD mounted under /Volumes (or uses --source).
  2. Copies the whole VIDEO_TS folder (movie, every audio language,
     subtitles, menus) to local storage, showing progress, speed and ETA.
     Scratched sectors are retried and, if still unreadable, zero-filled so
     the copy finishes instead of aborting.
  3. Optional (--mkv, needs ffmpeg): remuxes the main feature into a single
     .mkv file that keeps ALL audio tracks and subtitles with their language
     tags — no re-encoding, so no quality loss.

Usage
  python3 dvd_backup.py                 # copy VIDEO_TS to ~/Movies/<disc name>
  python3 dvd_backup.py --mkv           # ...and also build <disc name>.mkv
  python3 dvd_backup.py --mp4           # ONE compressed .mp4 with all audio languages
  python3 dvd_backup.py --dest ~/Desktop --mkv --eject

Only standard-library Python is used. ffmpeg is needed only for --mkv
(install with: brew install ffmpeg).

Note: this copies the disc as the Mac's filesystem presents it. It does not
remove copy protection (CSS); a backup of a CSS-protected commercial disc
will not play back.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time

SECTOR = 2048
CHUNK = SECTOR * 512  # 1 MiB reads
READ_RETRIES = 3


# ---------------------------------------------------------------- progress ---

def fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def fmt_time(sec):
    if sec is None or sec != sec or sec == float("inf"):
        return "--:--"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class Progress:
    """Single-line terminal progress bar: bar, %, size, speed, ETA, label."""

    def __init__(self, total, title):
        self.total = max(total, 1)
        self.done = 0
        self.start = time.monotonic()
        self.last_draw = 0.0
        self.label = ""
        print(f"\n{title}  ({fmt_bytes(total)})")

    def update(self, nbytes, label=None):
        self.done += nbytes
        if label is not None:
            self.label = label
        now = time.monotonic()
        if now - self.last_draw >= 0.2 or self.done >= self.total:
            self.last_draw = now
            self._draw(now)

    def _draw(self, now):
        width = shutil.get_terminal_size((100, 20)).columns
        frac = min(self.done / self.total, 1.0)
        elapsed = max(now - self.start, 1e-6)
        speed = self.done / elapsed
        eta = (self.total - self.done) / speed if speed > 0 else None
        stats = (f" {frac * 100:5.1f}%  {fmt_bytes(self.done)}/{fmt_bytes(self.total)}"
                 f"  {fmt_bytes(speed)}/s  ETA {fmt_time(eta)}  {self.label}")
        bar_w = max(10, min(40, width - len(stats) - 3))
        filled = int(bar_w * frac)
        line = "[" + "#" * filled + "-" * (bar_w - filled) + "]" + stats
        sys.stdout.write("\r" + line[: width - 1].ljust(width - 1))
        sys.stdout.flush()

    def finish(self):
        self._draw(time.monotonic())
        elapsed = time.monotonic() - self.start
        print(f"\nDone in {fmt_time(elapsed)}.")


# ------------------------------------------------------------ find the DVD ---

def find_dvd():
    """Return the first /Volumes/* that contains a VIDEO_TS folder."""
    root = "/Volumes"
    if not os.path.isdir(root):
        return None
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if os.path.isdir(os.path.join(path, "VIDEO_TS")):
            return path
    return None


def list_disc_files(source):
    """All files under VIDEO_TS/ and AUDIO_TS/ as (relative path, size)."""
    files = []
    for folder in ("VIDEO_TS", "AUDIO_TS"):
        base = os.path.join(source, folder)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            full = os.path.join(base, name)
            if os.path.isfile(full):
                files.append((os.path.join(folder, name), os.path.getsize(full)))
    return files


# ------------------------------------------------------------- disc copier ---

def read_chunk(f, offset, size):
    """Read `size` bytes at `offset`; on I/O errors retry sector by sector and
    zero-fill sectors that stay unreadable. Returns (data, bad_sectors)."""
    for _ in range(READ_RETRIES):
        try:
            f.seek(offset)
            return f.read(size), 0
        except OSError:
            time.sleep(0.2)

    data = bytearray()
    bad = 0
    for sec_off in range(offset, offset + size, SECTOR):
        n = min(SECTOR, offset + size - sec_off)
        for _ in range(READ_RETRIES):
            try:
                f.seek(sec_off)
                data += f.read(n)
                break
            except OSError:
                time.sleep(0.1)
        else:
            data += b"\0" * n
            bad += 1
    return bytes(data), bad


def copy_disc(source, dest_dir, files):
    total = sum(size for _, size in files)
    progress = Progress(total, f"Copying DVD: {source} -> {dest_dir}")
    bad_total = 0

    for rel, size in files:
        src = os.path.join(source, rel)
        dst = os.path.join(dest_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(src, "rb", buffering=0) as fin, open(dst, "wb") as fout:
            offset = 0
            while offset < size:
                data, bad = read_chunk(fin, offset, min(CHUNK, size - offset))
                if not data:
                    break  # file shorter than reported
                fout.write(data)
                offset += len(data)
                bad_total += bad
                progress.update(len(data), os.path.basename(rel))

    progress.finish()
    if bad_total:
        print(f"Warning: {bad_total} unreadable sector(s) were zero-filled "
              "(expect a short glitch at those spots).")
    return total


# ------------------------------------------------------- IFO language info ---

def read_title_langs(ifo_path):
    """Parse VTS_xx_0.IFO for audio / subtitle language codes.
    Returns ({audio_idx: 'en'}, {sub_idx: 'fr'})."""
    audio, subs = {}, {}
    try:
        with open(ifo_path, "rb") as f:
            data = f.read(0x400)
    except OSError:
        return audio, subs
    if len(data) < 0x356 or data[:12] != b"DVDVIDEO-VTS":
        return audio, subs

    def lang(entry):
        code = entry[2:4]
        if code.isalpha():
            return code.decode("ascii").lower()
        return None

    n_audio = min(int.from_bytes(data[0x202:0x204], "big"), 8)
    for i in range(n_audio):
        l = lang(data[0x204 + i * 8: 0x204 + i * 8 + 8])
        if l:
            audio[i] = l
    n_subs = min(int.from_bytes(data[0x254:0x256], "big"), 32)
    for i in range(n_subs):
        l = lang(data[0x256 + i * 6: 0x256 + i * 6 + 6])
        if l:
            subs[i] = l
    return audio, subs


# 2-letter (ISO 639-1, used on DVDs) -> 3-letter (ISO 639-2, used by MKV)
ISO639 = {
    "en": "eng", "fr": "fre", "de": "ger", "es": "spa", "it": "ita",
    "ja": "jpn", "ko": "kor", "zh": "chi", "ru": "rus", "pt": "por",
    "nl": "dut", "sv": "swe", "da": "dan", "no": "nor", "fi": "fin",
    "pl": "pol", "cs": "cze", "hu": "hun", "el": "gre", "tr": "tur",
    "he": "heb", "ar": "ara", "th": "tha", "hi": "hin", "is": "ice",
}


# ----------------------------------------------------- one-file conversion ---

def main_title_vobs(video_ts):
    """Pick the title set with the most video (the main feature) and return
    its VTS_xx_1.VOB ... VTS_xx_N.VOB paths plus its IFO path."""
    sets = {}
    for name in os.listdir(video_ts):
        up = name.upper()
        if up.startswith("VTS_") and up.endswith(".VOB") and not up.endswith("_0.VOB"):
            vts = up[4:6]
            sets.setdefault(vts, []).append(name)
    if not sets:
        return None, [], 0

    def set_size(vts):
        return sum(os.path.getsize(os.path.join(video_ts, n)) for n in sets[vts])

    best = max(sets, key=set_size)
    vobs = sorted(sets[best], key=lambda n: int(n.upper().split("_")[2].split(".")[0]))
    ifo = os.path.join(video_ts, f"VTS_{best}_0.IFO")
    return ifo, [os.path.join(video_ts, n) for n in vobs], set_size(best)


STREAM_RE = re.compile(r"Stream #0:\d+\[0x([0-9a-fA-F]+)\]: (Video|Audio|Subtitle)")


def probe_streams(ffmpeg, first_vob):
    """List (stream_id, 'video'|'audio'|'subtitle') using `ffmpeg -i`."""
    out = subprocess.run(
        [ffmpeg, "-hide_banner", "-analyzeduration", "200M", "-probesize", "200M",
         "-f", "mpeg", "-i", first_vob],
        capture_output=True, text=True)
    return [(int(m.group(1), 16), m.group(2).lower())
            for m in STREAM_RE.finditer(out.stderr)]


def make_movie(video_ts, out_path, fmt, fast=False):
    """Turn the main feature into ONE file with every audio language.
    fmt='mp4': compressed H.264 + AAC (plays in QuickTime), subtitles dropped.
    fmt='mkv': lossless copy of video/audio/subtitles (VLC / IINA)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("\nffmpeg not found. Install it with: brew install ffmpeg")
        return False

    ifo, vobs, total = main_title_vobs(video_ts)
    if not vobs:
        print("\nNo title VOBs found on the disc.")
        return False
    audio_langs, sub_langs = read_title_langs(ifo)

    maps, meta = [], []
    out_a = out_s = 0
    for sid, ctype in probe_streams(ffmpeg, vobs[0]):
        if ctype == "video":
            maps.append(f"0:i:{hex(sid)}")
        elif ctype == "audio":
            maps.append(f"0:i:{hex(sid)}")
            l = audio_langs.get(sid & 0x07)
            if l:
                meta += [f"-metadata:s:a:{out_a}", f"language={ISO639.get(l, l)}"]
            out_a += 1
        elif ctype == "subtitle" and fmt == "mkv":
            maps.append(f"0:i:{hex(sid)}")
            l = sub_langs.get(sid & 0x1F)
            if l:
                meta += [f"-metadata:s:s:{out_s}", f"language={ISO639.get(l, l)}"]
            out_s += 1
    if not maps:  # probe failed: take all video + audio
        maps = ["0:v?", "0:a?"] + (["0:s?"] if fmt == "mkv" else [])

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-fflags", "+genpts", "-analyzeduration", "200M", "-probesize", "200M",
           "-f", "mpeg", "-i", "pipe:0"]
    for m in maps:
        cmd += ["-map", m]
    if fmt == "mp4":
        if fast and sys.platform == "darwin":
            video = ["-c:v", "h264_videotoolbox", "-b:v", "4M"]  # Mac hardware encoder
        else:
            video = ["-c:v", "libx264", "-crf", "20", "-preset", "medium"]
        cmd += video + ["-vf", "yadif=deint=interlaced", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "320k",
                        "-disposition:a", "0", "-disposition:a:0", "default",
                        "-movflags", "+faststart"]
    else:
        cmd += ["-c", "copy"]
    cmd += meta + [out_path]

    print(f"\nMain feature: {len(vobs)} VOB file(s), {out_a} audio track(s)"
          + (f", {out_s} subtitle track(s)" if fmt == "mkv" else ""))
    if audio_langs:
        print("Audio languages: " + ", ".join(audio_langs[k] for k in sorted(audio_langs)))

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    verb = "Compressing to MP4" if fmt == "mp4" else "Building MKV"
    progress = Progress(total, f"{verb} -> {out_path}")
    bad_total = 0
    try:
        for vob in vobs:
            size = os.path.getsize(vob)
            with open(vob, "rb", buffering=0) as f:
                offset = 0
                while offset < size:
                    data, bad = read_chunk(f, offset, min(CHUNK, size - offset))
                    if not data:
                        break
                    proc.stdin.write(data)
                    offset += len(data)
                    bad_total += bad
                    progress.update(len(data), os.path.basename(vob))
        proc.stdin.close()
    except BrokenPipeError:
        pass
    rc = proc.wait()
    progress.finish()
    if bad_total:
        print(f"Warning: {bad_total} unreadable sector(s) were skipped.")
    if rc != 0:
        print(f"ffmpeg exited with code {rc}; the file may be incomplete.")
        return False
    return True


# -------------------------------------------------------------------- main ---

def main():
    ap = argparse.ArgumentParser(description="Back up a DVD to local storage with progress.")
    ap.add_argument("--source", help="DVD mount point (default: auto-detect under /Volumes)")
    ap.add_argument("--dest", default=os.path.expanduser("~/Movies"),
                    help="folder to save into (default: ~/Movies)")
    ap.add_argument("--name", help="backup name (default: disc volume name)")
    ap.add_argument("--mp4", action="store_true",
                    help="save ONE compressed .mp4 (H.264 + all audio languages) instead of "
                         "copying the disc folder (needs ffmpeg)")
    ap.add_argument("--fast", action="store_true",
                    help="with --mp4: use the Mac's hardware encoder (much faster, bigger file)")
    ap.add_argument("--mkv", action="store_true",
                    help="also make one lossless .mkv of the main feature (needs ffmpeg)")
    ap.add_argument("--eject", action="store_true", help="eject the disc when done")
    args = ap.parse_args()

    source = args.source or find_dvd()
    if not source or not os.path.isdir(os.path.join(source, "VIDEO_TS")):
        sys.exit("No DVD found. Insert a video DVD (it should appear in Finder), "
                 "or pass --source /Volumes/<DISC>.")

    name = args.name or os.path.basename(os.path.normpath(source)) or "DVD"
    dest_root = os.path.expanduser(args.dest)
    os.makedirs(dest_root, exist_ok=True)
    print(f"Disc:        {source}")

    try:
        if args.mp4:
            # Encode straight from the disc into a single file.
            out_path = os.path.join(dest_root, f"{name}.mp4")
            _, _, title_size = main_title_vobs(os.path.join(source, "VIDEO_TS"))
            free = shutil.disk_usage(dest_root).free
            if free < title_size:
                sys.exit(f"Not enough free space: need up to {fmt_bytes(title_size)}, "
                         f"have {fmt_bytes(free)}.")
            print(f"Output:      {out_path}")
            ok = make_movie(os.path.join(source, "VIDEO_TS"), out_path, "mp4", args.fast)
            result = (f"\nSaved: {out_path}\nPlay it: double-click (QuickTime). "
                      "Switch language via View > Languages (or Audio menu in VLC/IINA)."
                      if ok else "\nMP4 was not created.")
        else:
            dest_dir = os.path.join(dest_root, name)
            files = list_disc_files(source)
            total = sum(size for _, size in files)
            os.makedirs(dest_dir, exist_ok=True)
            need = total * (2 if args.mkv else 1)
            free = shutil.disk_usage(dest_dir).free
            if free < need:
                sys.exit(f"Not enough free space: need {fmt_bytes(need)}, have {fmt_bytes(free)}.")
            print(f"Destination: {dest_dir}")
            print(f"Files:       {len(files)}  ({fmt_bytes(total)})")
            copy_disc(source, dest_dir, files)
            made_mkv = args.mkv and make_movie(os.path.join(dest_dir, "VIDEO_TS"),
                                               os.path.join(dest_dir, f"{name}.mkv"), "mkv")
            result = (f"\nBackup saved to: {dest_dir}\nPlay it: open the VIDEO_TS folder in VLC or IINA"
                      + (", or open the .mkv file." if made_mkv else "."))
    except KeyboardInterrupt:
        sys.exit("\nCancelled.")

    if args.eject and sys.platform == "darwin":
        subprocess.run(["drutil", "eject"], check=False)
    print(result)


if __name__ == "__main__":
    main()
