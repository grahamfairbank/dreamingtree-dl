import shutil
import subprocess
from pathlib import Path

import pytest

from app.convert import ConversionError, to_flac

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def make_wav(path: Path, rate: int = 44100, bits: int = 16, secs: float = 1.0) -> Path:
    codec = {16: "pcm_s16le", 24: "pcm_s24le"}[bits]
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={rate}",
                    "-t", str(secs), "-c:a", codec, str(path)], check=True)
    return path


class _Bits:
    def __init__(self):
        self.bits: list[int] = []

    def put(self, v: int, n: int):
        for i in range(n - 1, -1, -1):
            self.bits.append((v >> i) & 1)

    def uvar(self, v: int, k: int):  # Shorten's Rice-style code: unary quotient (zeros then 1), k low bits
        q = v >> k
        self.bits.extend([0] * q + [1])
        if k:
            self.put(v & ((1 << k) - 1), k)

    def ulong(self, v: int):
        k = max(v.bit_length(), 1)
        self.uvar(k, 2)
        self.uvar(v, k)

    def svar(self, v: int, k: int):
        self.uvar((v << 1) if v >= 0 else (((~v) << 1) | 1), k + 1)

    def tobytes(self) -> bytes:
        b = self.bits + [0] * (-len(self.bits) % 8)
        return bytes(int("".join(map(str, b[i:i + 8])), 2) for i in range(0, len(b), 8))


def make_shn(path: Path, rate: int = 44100, blocks: int = 40) -> Path:
    """Write a mono 16-bit Shorten v2 file (DIFF0 blocks, no prediction) holding a quiet sine wave.

    Hand-built because no `shorten` encoder exists in the dev image; ffmpeg decodes the format.
    """
    import math
    import struct
    n = blocks * 256
    samples = [int(8000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(n)]
    hdr = (b"RIFF" + struct.pack("<I", 36 + 2 * n) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
           + b"data" + struct.pack("<I", 2 * n))
    w = _Bits()
    w.put(int.from_bytes(b"ajkg", "big"), 32)
    w.put(2, 8)             # version
    w.ulong(5)              # filetype: s16 little endian
    w.ulong(1)              # channels
    w.ulong(256)            # blocksize
    w.ulong(0)              # max lpc order
    w.ulong(0)              # nmean
    w.ulong(0)              # bytes to skip
    w.uvar(9, 2)            # FN_VERBATIM: the WAV header
    w.uvar(len(hdr), 5)
    for byte in hdr:
        w.uvar(byte, 8)
    for b in range(blocks):
        w.uvar(0, 2)        # FN_DIFF0
        w.uvar(12, 3)       # energy
        for v in samples[b * 256:(b + 1) * 256]:
            w.svar(v, 12)
    w.uvar(4, 2)            # FN_QUIT
    path.write_bytes(w.tobytes())
    return path


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=codec_name,sample_rate,bits_per_raw_sample,channels", "-of", "default=nw=1", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return dict(l.split("=", 1) for l in out.splitlines())


def test_wav_16_44_to_flac(tmp_path):
    src = make_wav(tmp_path / "t01.wav")
    out = to_flac(src, tmp_path / "out")
    assert out == tmp_path / "out" / "t01.flac" and out.stat().st_size > 0
    p = probe(out)
    assert (p["codec_name"], p["sample_rate"], p["bits_per_raw_sample"]) == ("flac", "44100", "16")
    assert src.exists()  # the source is untouched


def test_wav_24_48_keeps_rate_and_depth(tmp_path):
    out = to_flac(make_wav(tmp_path / "a.wav", rate=48000, bits=24), tmp_path / "out")
    p = probe(out)
    assert (p["sample_rate"], p["bits_per_raw_sample"]) == ("48000", "24")


def test_output_is_valid_flac(tmp_path):
    if not shutil.which("flac"):
        pytest.skip("flac not installed")
    out = to_flac(make_wav(tmp_path / "a.wav"), tmp_path / "out")
    subprocess.run(["flac", "-t", "-s", str(out)], check=True)


def test_creates_dest_dir_and_leaves_no_partial(tmp_path):
    out = to_flac(make_wav(tmp_path / "a.wav"), tmp_path / "x" / "y")
    assert [p.name for p in out.parent.iterdir()] == ["a.flac"]


def test_failure_raises_and_cleans_up(tmp_path):
    bad = tmp_path / "bad.shn"
    bad.write_bytes(b"this is not audio")
    with pytest.raises(ConversionError):
        to_flac(bad, tmp_path / "out")
    assert not list((tmp_path / "out").glob("*"))


def test_missing_source_raises(tmp_path):
    with pytest.raises(ConversionError):
        to_flac(tmp_path / "nope.wav", tmp_path / "out")


def test_ape_when_encoder_available(tmp_path):
    """Needs an APE encoder (mac); ffmpeg cannot create APE, so only run if `mac` exists."""
    if not shutil.which("mac"):
        pytest.skip("APE encoder (mac) not available; APE decode unverified")
    wav = make_wav(tmp_path / "a.wav")
    ape = tmp_path / "a.ape"
    subprocess.run(["mac", str(wav), str(ape), "-c2000"], check=True)
    assert to_flac(ape, tmp_path / "out").exists()


def test_shn_handbuilt_decodes(tmp_path):
    """A synthetic Shorten file (see make_shn) round-trips through ffmpeg to a valid FLAC."""
    shn = make_shn(tmp_path / "d1t01.shn")
    out = to_flac(shn, tmp_path / "out")
    p = probe(out)
    assert (p["codec_name"], p["sample_rate"], p["bits_per_raw_sample"]) == ("flac", "44100", "16")
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(out), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    assert len(raw) == 40 * 256 * 2
    assert max(abs(int.from_bytes(raw[i:i + 2], "little", signed=True)) for i in range(0, len(raw), 2)) > 7000


def test_shn_real_sample_when_available(tmp_path):
    """Real tape-trading SHN, if one is present under tests/fixtures/samples."""
    samples = list((Path(__file__).parent / "fixtures" / "samples").glob("**/*.shn"))
    if not samples:
        pytest.skip("no .shn sample available; SHN conversion unverified")
    out = to_flac(samples[0], tmp_path / "out")
    assert probe(out)["codec_name"] == "flac"
