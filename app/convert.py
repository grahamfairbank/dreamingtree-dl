"""Convert SHN/APE/WAV to FLAC with ffmpeg."""
from __future__ import annotations

import subprocess
from pathlib import Path

SOURCE_EXTS = {".shn", ".ape", ".wav", ".wv", ".aiff", ".aif"}
FFMPEG_TIMEOUT = 1800


class ConversionError(RuntimeError):
    pass


def _probe_sample_fmt(src: Path) -> str | None:
    """ffprobe's sample_fmt for the first audio stream (s16, s32, ...), or None if unknown."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_fmt,bits_per_raw_sample",
             "-of", "default=nw=1", str(src)],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    info = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
    bits = info.get("bits_per_raw_sample", "")
    if bits.isdigit() and int(bits) in (8, 16, 24, 32):
        return f"s{bits}" if int(bits) != 8 else "u8"
    fmt = info.get("sample_fmt", "")
    return fmt or None


def to_flac(src: Path, dest_dir: Path) -> Path:
    """Decode `src` (SHN, APE, WAV...) to `dest_dir/<stem>.flac`, keeping sample rate and bit depth.

    Raises ConversionError if ffmpeg fails or produces no file. The source file is never modified.
    """
    src = Path(src)
    dest_dir = Path(dest_dir)
    if not src.is_file():
        raise ConversionError(f"source not found: {src}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (src.stem + ".flac")
    tmp = dest_dir / (src.stem + ".part.flac")
    fmt = _probe_sample_fmt(src)
    # FLAC stores 8/16/24 bit; 24-bit sources decode as s32 so request s32 and let the encoder record 24 bits.
    sample_fmt = {"s16": "s16", "s16p": "s16", "u8": "s16", "s24": "s32", "s32": "s32", "s32p": "s32"}.get(fmt or "", None)
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src), "-vn", "-map_metadata", "-1"]
    if sample_fmt:
        cmd += ["-sample_fmt", sample_fmt]
    cmd += ["-c:a", "flac", "-compression_level", "8", str(tmp)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        tmp.unlink(missing_ok=True)
        raise ConversionError(f"ffmpeg could not run on {src.name}: {e}") from e
    if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise ConversionError(f"ffmpeg failed on {src.name}: {(r.stderr or '').strip()[-500:]}")
    tmp.replace(dest)
    return dest
