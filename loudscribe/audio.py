"""Small ffmpeg helpers."""
import re
import subprocess

import numpy as np


class Error(Exception):
    """ffmpeg couldn't read or write a file; the message is ffmpeg's first error line (the cause)."""


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode:
        err = r.stderr.decode("utf-8", "replace").strip().splitlines()
        raise Error(first_error(err) or f"{cmd[0]} failed (exit code {r.returncode})")
    return r.stdout


def first_error(lines):
    """ffmpeg's first error line (the cause), without its "[stream @ 0x…] " prefix."""
    return re.sub(r"^\[[^]]*\] ", "", lines[0]) if lines else ""


def ffmpeg(*args):
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args])


def duration(path):
    """Length in seconds, or 0.0 if ffprobe can't read the file (e.g. still copying)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def sample_rate(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                        "stream=sample_rate", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return int(r.stdout.strip() or 44100)


def channels(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                        "stream=channels", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return int(r.stdout.strip() or 1)


def to_mono_args(path):
    """Average two channels to one (plain -ac 1 sums them, +3 dB); mono passes through."""
    if channels(path) == 2:
        return ["-af", "pan=mono|c0=0.5*c0+0.5*c1"]
    return ["-ac", "1"]


def load_mono(path, sr):
    raw = _run(["ffmpeg", "-loglevel", "error", "-nostdin", "-i", str(path), "-ac", "1",
                "-ar", str(sr), "-f", "f32le", "-"])
    return np.frombuffer(raw, dtype=np.float32)
