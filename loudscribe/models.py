"""Which engine this machine uses, the model names, downloading them, and a health check."""
import json
import logging
import os
import platform
import shutil
from pathlib import Path

# The Python environment and the music-removal model live outside the code folder, so the skill
# can be copied between Claude Code and Codex without duplicating them. Whisper and the
# classifier go in the usual Hugging Face cache (~/.cache/huggingface).
HOME = Path(os.environ.get("LOUDSCRIBE_HOME", Path.home() / ".loudscribe"))
MODELS_DIR = HOME / "models"
TMP_DIR = HOME / "tmp"

# Transcription: Whisper large-v3-turbo. Music removal: MelBand-RoFormer (keeps speech, drops music).
MLX_WHISPER_REPO = "mlx-community/whisper-large-v3-turbo"  # Apple Silicon (MLX)
# Everything else (faster-whisper). The repo moved owners in 2026; installs from before keep using
# their cached copy under the old name.
CT2_WHISPER_REPOS = ("dropbox-dash/faster-whisper-large-v3-turbo",
                     "mobiuslabsgmbh/faster-whisper-large-v3-turbo")
SEPARATOR_MODEL = "vocals_mel_band_roformer.ckpt"
AST_REPO = "MIT/ast-finetuned-audioset-10-10-0.4593"
REQUIRED_FILES = {  # a download counts as complete only with all of these
    MLX_WHISPER_REPO: ("config.json", "weights.safetensors"),
    CT2_WHISPER_REPOS[0]: ("config.json", "model.bin", "tokenizer.json", "vocabulary.json"),
    CT2_WHISPER_REPOS[1]: ("config.json", "model.bin", "tokenizer.json", "vocabulary.json"),
    AST_REPO: ("config.json", "model.safetensors", "preprocessor_config.json"),
}

# Seconds of processing per second of audio (low, high), used until this computer has a measured
# speed of its own. CPU-only: ~0.75 on a fast Apple chip, 2.5–3.2 on a typical Windows laptop.
SPEED = {"mlx": (0.33, 0.66), "cuda": (0.25, 0.6), "cpu": (2.5, 3.5)}
QUIET = 0.4  # share of that time left without music removal
SPEED_FILE = HOME / "speed.json"  # measured by earlier runs on this computer
LABEL = {"mlx": "Apple Silicon GPU", "cuda": "NVIDIA GPU (CUDA)",
         "cpu": "CPU only (no supported GPU): expect roughly 2.5–3.5 hours per hour of audio"}


def backend():
    """'mlx' on Apple Silicon Macs, 'cuda' with an NVIDIA GPU, otherwise 'cpu'.
    LOUDSCRIBE_BACKEND overrides it (e.g. to test the CPU path on a Mac)."""
    forced = os.environ.get("LOUDSCRIBE_BACKEND")
    if forced in SPEED:
        return forced
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "mlx"
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"


def whisper_repo():
    if backend() == "mlx":
        return MLX_WHISPER_REPO
    return next((r for r in CT2_WHISPER_REPOS if _hf_cached(r)), CT2_WHISPER_REPOS[0])


def speed(engine, music=True):
    """Seconds of processing per second of audio as (low, high, measured): from this computer's
    last full run when there is one, else the typical figures above."""
    try:
        s = json.loads(SPEED_FILE.read_text(encoding="utf-8"))[f"{engine}-{'music' if music else 'quiet'}"]
        return s, s * 1.25, True
    except (OSError, ValueError, KeyError):
        lo, hi = SPEED[engine]
        return (lo, hi, False) if music else (lo * QUIET, hi * QUIET, False)


def remember_speed(engine, music, ratio):
    try:
        data = json.loads(SPEED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[f"{engine}-{'music' if music else 'quiet'}"] = round(ratio, 3)
    HOME.mkdir(parents=True, exist_ok=True)
    SPEED_FILE.write_text(json.dumps(data), encoding="utf-8")


def hf_hub_dir():
    return Path(os.environ.get("HF_HUB_CACHE") or
                Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub")


def _hf_cached(repo):
    """A snapshot with every required file present (an interrupted download leaves some missing)."""
    snaps = hf_hub_dir() / f"models--{repo.replace('/', '--')}" / "snapshots"
    return snaps.is_dir() and any(all((d / f).exists() for f in REQUIRED_FILES[repo])
                                  for d in snaps.iterdir())


def separator_cached():
    files = [MODELS_DIR / SEPARATOR_MODEL, MODELS_DIR / SEPARATOR_MODEL.replace(".ckpt", ".yaml")]
    return all(f.exists() and f.stat().st_size for f in files)


def all_cached(music_removal=True):
    return (_hf_cached(whisper_repo()) and _hf_cached(AST_REPO)
            and (separator_cached() or not music_removal))


def download(music_removal=True):
    """Fetch the models (about 3 GB). Audio never leaves the machine; this only downloads."""
    from huggingface_hub import snapshot_download
    print(f"  speech recognition (Whisper large-v3 turbo, ~1.6 GB) for: {LABEL[backend()].split(':')[0]}")
    snapshot_download(whisper_repo())
    print("  speech-vs-singing classifier (AST, ~350 MB)")
    from transformers import ASTFeatureExtractor, ASTForAudioClassification
    ASTFeatureExtractor.from_pretrained(AST_REPO)
    ASTForAudioClassification.from_pretrained(AST_REPO)
    if not music_removal:
        return
    print("  music removal (MelBand-RoFormer, ~900 MB)")
    from audio_separator.separator import Separator
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    Separator(model_file_dir=str(MODELS_DIR), output_dir=str(MODELS_DIR),
              log_level=logging.WARNING).load_model(model_filename=SEPARATOR_MODEL)


def ram_gb():
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
    except (AttributeError, ValueError, OSError):  # Windows
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                        ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                        ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                        ("avail_ext", ctypes.c_ulonglong)]
        status = MemoryStatus(length=ctypes.sizeof(MemoryStatus))
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return status.total / 1e9


def doctor(music_removal=True):
    """Print checks; return True if everything needed to process (with or without music removal)
    is in place."""
    ok = True

    def check(cond, good, bad):
        nonlocal ok
        print(f"  {'ok ' if cond else 'FIX'}  {good if cond else bad}")
        ok &= bool(cond)

    from . import __version__
    be = backend()
    print(f"  ...  loudscribe {__version__}, Python {platform.python_version()}")
    print(f"  ...  engine: {LABEL[be]}")
    check(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg installed",
          "ffmpeg missing: install it (see SKILL.md, Install)")
    try:
        import numpy, soundfile, torch, transformers  # noqa: F401, E401
        if be == "mlx":
            import mlx_whisper  # noqa: F401
        else:
            import faster_whisper  # noqa: F401
        check(True, "Python packages installed", "")
    except ImportError as e:
        check(False, "", f"Python package missing ({e.name}): pip install -r requirements.txt (see SKILL.md)")
    check(all_cached(music_removal=False), "models downloaded",
          "models not downloaded yet: run `python -m loudscribe setup`")
    try:
        import audio_separator  # noqa: F401
        has_sep = separator_cached()
    except ImportError:
        has_sep = False
    if music_removal:
        check(has_sep, "music removal ready", "music removal not installed: run `python -m loudscribe setup` "
              "(or check with --no-music-removal for quiet places)")
    else:
        print(f"  {'ok ' if has_sep else 'off'}  music removal {'ready' if has_sep else 'not installed'}")
    free_gb = shutil.disk_usage(Path.home()).free / 1e9
    check(free_gb > 10, f"{free_gb:.0f} GB free disk space",
          f"only {free_gb:.0f} GB free: processing needs roughly 2 GB per hour of audio while it runs")
    ram = ram_gb()
    print(f"  {'ok ' if ram >= 15 else 'low'}  {ram:.0f} GB memory"
          + ("" if ram >= 15 else " (music removal uses ~7 GB: close other apps, expect it to be slower)"))
    return ok
