"""One recording: convert -> remove music -> clean up -> transcribe -> find conversations.

Each stage writes a .done marker in the recording's work folder, with the settings it ran with,
so an interrupted run picks up where it stopped and changing a setting redoes only the steps it
affects. Big temporary WAVs are deleted as soon as the next step has used them.
"""
import datetime as dt
import gc
import hashlib
import json
import logging
import platform
import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from . import audio, models
from . import classify as C
from .speech_singing import cached_scorer

SR = 44100
STAGES = ["convert", "separate", "polish", "transcribe", "classify"]
STAGE_LABELS = {"convert": "converting", "separate": "removing music", "polish": "cleaning up audio",
                "transcribe": "transcribing", "classify": "finding conversations"}
SETTING_STAGE = {"separated": "separate", "language": "transcribe"}  # first step each setting changes
CHUNK_S = 600         # music removal works in 10-minute pieces to bound memory
SECTION_PAD_S = 0.6   # audio kept either side of a section in the conversation-only file
SECTION_GAP_S = 1.0   # silence before each section in the conversation-only file
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aiff", ".aif"}


@dataclass
class Recording:
    src: Path
    id: str
    start: dt.datetime
    start_source: str     # "filename" or "file modified time"
    duration: float


def _start_time(path, duration):
    """Recorders name files after their start time (20260131223000.WAV) and save them when they
    stop, so without a time in the name the start is the modified time minus the length."""
    m = re.search(r"20\d{12}", path.stem)
    if m:
        try:
            return dt.datetime.strptime(m.group(), "%Y%m%d%H%M%S"), "filename"
        except ValueError:  # 14 digits that aren't a date
            pass
    return (dt.datetime.fromtimestamp(path.stat().st_mtime) - dt.timedelta(seconds=duration),
            "file modified time")


def find_recordings(folder):
    """Audio files in a folder, oldest first. Unreadable files are reported and skipped."""
    recs, skipped = [], []
    for p in sorted(folder.iterdir()):
        if p.name.startswith(".") or p.suffix.lower() not in AUDIO_EXTS or not p.is_file():
            continue
        dur = audio.duration(p) if p.stat().st_size else 0.0
        if dur < 1.0:
            skipped.append(p.name)
            continue
        start, source = _start_time(p, dur)
        base = re.sub(r"[^\w.-]+", "_", p.stem)
        taken = {r.id.lower() for r in recs}  # lower-case: file systems that ignore case
        rid, n = base, 1
        if rid.lower() in taken:  # night.wav and night.m4a side by side
            rid = f"{base}_{p.suffix.lstrip('.').lower()}"
        while rid.lower() in taken:  # "a!.wav" and "a?.wav" both sanitise to "a_"
            n += 1
            rid = f"{base}_{n}"
        recs.append(Recording(p, rid, start.replace(microsecond=0), source, dur))
    return sorted(recs, key=lambda r: r.start), skipped


_separator = None


def _remove_music(rec, work):
    global _separator
    from audio_separator.separator import Separator
    models.TMP_DIR.mkdir(parents=True, exist_ok=True)
    if _separator is None:
        def make():
            # chunk_duration bounds memory on long files (10-minute pieces).
            sep = Separator(model_file_dir=str(models.MODELS_DIR), output_dir=str(models.TMP_DIR),
                            output_single_stem="Vocals", chunk_duration=CHUNK_S, log_level=logging.WARNING)
            sep.load_model(model_filename=models.SEPARATOR_MODEL)
            return sep
        if models.backend() == "cpu" and platform.system() == "Darwin":
            # Test mode on a Mac (LOUDSCRIBE_BACKEND=cpu): hide the Apple GPU while the separator
            # picks its device, then put the real check back. Leaving a stand-in in place crashes
            # PyTorch's compiler later (it inspects this function), e.g. when the classifier loads.
            import torch
            real = torch.backends.mps.is_available
            torch.backends.mps.is_available = lambda: False
            try:
                _separator = make()
            finally:
                torch.backends.mps.is_available = real
        else:
            _separator = make()
    # The separator cuts fixed 10-minute chunks, so an A20 hour file (3600.02 s) ends in a 0.02 s
    # chunk that crashes the model. Pad the end with silence so the last chunk is >= 10 s, then
    # trim the vocals back to the recording's length.
    src = work / "input_44k.wav"
    tail = sf.info(src).frames % (CHUNK_S * SR)
    if 0 < tail < 10 * SR:
        padded = work / "input_padded.wav"
        audio.ffmpeg("-i", str(src), "-af", f"apad=pad_len={10 * SR - tail}", "-c:a", "pcm_f32le",
                     str(padded))
        padded.replace(src)
    (out,) = _separator.separate(str(src), {"Vocals": f"{rec.id}_vocals"})
    out = Path(out) if Path(out).is_absolute() else models.TMP_DIR / out
    x, _ = sf.read(out, dtype="float32", always_2d=True)
    sf.write(work / "vocals.wav", x[:round(rec.duration * SR)].mean(axis=1), SR, subtype="FLOAT")
    out.unlink()


def release(stage):
    """Unload the model a finished step used. Steps run across all recordings one at a time, so
    only one big model is in memory at once (separation alone peaks at ~7 GB; running each
    recording start-to-finish stacked the models up to 13 GB)."""
    global _separator, _fw
    if stage == "separate" and _separator is not None:
        _separator = None
    elif stage == "transcribe":
        _fw = None
        try:
            from mlx_whisper.transcribe import ModelHolder
            ModelHolder.model = ModelHolder.model_path = None
        except ImportError:
            pass
    elif stage == "classify":  # the next folder starts with music removal again
        from . import speech_singing
        speech_singing._model = speech_singing._fe = None
    else:
        return
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        elif torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except ImportError:
        pass
    try:
        import mlx.core as mx
        (getattr(mx, "clear_cache", None) or mx.metal.clear_cache)()
    except (ImportError, AttributeError):
        pass


_fw = None  # (faster-whisper model, the device it's on)


def _transcribe(src, dst, language, log=print):
    common = dict(language=language, word_timestamps=True,
                  condition_on_previous_text=False)  # stops one hallucination seeding the next
    if models.backend() == "mlx":
        import mlx_whisper
        segs = mlx_whisper.transcribe(str(src), path_or_hf_repo=models.MLX_WHISPER_REPO,
                                      task="transcribe", verbose=None, **common)["segments"]
    else:
        segs = _transcribe_ct2(src, common, log)
    dst.write_text(json.dumps(segs), encoding="utf-8")


def _transcribe_ct2(src, common, log):
    """faster-whisper (Windows, Linux, CPU). Greedy decoding like mlx-whisper's default; same
    temperature fallback and thresholds. Returns segments shaped like mlx-whisper's."""
    global _fw
    from faster_whisper import WhisperModel

    def load(device):
        return WhisperModel(models.whisper_repo(), device=device,
                            compute_type="float16" if device == "cuda" else "int8"), device

    def run():
        segments, _ = _fw[0].transcribe(str(src), beam_size=1, vad_filter=False, **common)
        return list(segments)  # decoding is lazy; GPU library errors surface here

    if _fw is None:
        if models.backend() != "cuda":
            _fw = load("cpu")
        else:
            try:
                _fw = load("cuda")
            except (RuntimeError, ValueError) as e:  # e.g. CUDA libraries missing
                log(f" (GPU transcription unavailable: {str(e)[:80]}; using the CPU)", end="")
                _fw = load("cpu")
    try:
        segments = run()
    except (RuntimeError, OSError) as e:
        if _fw[1] != "cuda":
            raise
        log(f" (GPU transcription failed: {str(e)[:80]}; using the CPU)", end="")
        _fw = load("cpu")
        segments = run()
    return [{"start": s.start, "end": s.end, "text": s.text, "avg_logprob": s.avg_logprob,
             "no_speech_prob": s.no_speech_prob, "compression_ratio": s.compression_ratio,
             "words": [{"word": w.word, "start": w.start, "end": w.end, "probability": w.probability}
                       for w in (s.words or [])]} for s in segments]


def _hms(t0, sec):
    return (t0 + dt.timedelta(seconds=sec)).strftime("%H:%M:%S")


def _find_conversations(rec, work, cleaned, conv_m4a):
    segs = json.loads((work / "whisper.json").read_text(encoding="utf-8"))
    segs = C.classify(segs, cached_scorer(cleaned, work / "ast.json"))
    secs = C.sections(segs)

    with open(work / "segments.tsv", "w", encoding="utf-8") as f:  # every segment and why it got its label
        f.write("wall\tstart_s\tdur_s\tlabel\tno_speech\tlogprob\tcompr\treasons\ttext\n")
        for s in segs:
            f.write(f"{_hms(rec.start, s['start'])}\t{s['start']:.1f}\t{s['end'] - s['start']:.1f}\t"
                    f"{s['label']}\t{s['no_speech_prob']:.2f}\t{s['avg_logprob']:.2f}\t"
                    f"{s['compression_ratio']:.2f}\t{'; '.join(s['reasons'])}\t{s['text']}\n")

    # conversation-only audio: [1 s silence][section][1 s silence][section]...
    samples = audio.load_mono(cleaned, SR)
    gap = np.zeros(int(SECTION_GAP_S * SR), dtype=np.float32)
    parts, pos, sections, lines = [], 0.0, [], []
    for n, sec in enumerate(secs, 1):
        a = max(0.0, sec[0]["start"] - SECTION_PAD_S)
        b = min(len(samples) / SR, sec[-1]["end"] + SECTION_PAD_S)
        clip = samples[int(a * SR): int(b * SR)].copy()
        fade = min(int(0.05 * SR), len(clip) // 2)
        if fade:
            clip[:fade] *= np.linspace(0, 1, fade)
            clip[-fade:] *= np.linspace(1, 0, fade)
        clip_start = pos + SECTION_GAP_S
        sections.append({"n": n, "start": sec[0]["start"], "end": sec[-1]["end"], "t_conv": clip_start,
                         "wall_start": _hms(rec.start, sec[0]["start"]),
                         "wall_end": _hms(rec.start, sec[-1]["end"])})
        lines += [{"section": n, "t": round(s["start"], 2), "end": round(s["end"], 2),
                   "t_conv": round(clip_start + s["start"] - a, 2),
                   "wall": _hms(rec.start, s["start"]), "text": s["text"]} for s in sec]
        parts += [gap, clip]
        pos = clip_start + len(clip) / SR
    if parts:
        tmp = work / "conversation.wav"
        sf.write(tmp, np.concatenate(parts), SR, subtype="PCM_16")
        audio.ffmpeg("-i", str(tmp), "-c:a", "aac", "-b:a", "128k", str(conv_m4a))
        tmp.unlink()
    elif conv_m4a.exists():
        conv_m4a.unlink()
    speech = sum(s["end"] - s["start"] for sec in secs for s in sec)
    (work / "recording.json").write_text(json.dumps({
        "id": rec.id, "source": str(rec.src), "start": rec.start.isoformat(),
        "start_source": rec.start_source, "duration": rec.duration, "speech_seconds": speech,
        "sections": sections, "lines": lines}, indent=1), encoding="utf-8")


class Job:
    """One recording's steps. __main__ runs each step for every recording before the next step."""

    def __init__(self, rec, session_dir, *, separate=True, language="en", redo=None, keep=False):
        self.rec, self.keep = rec, keep
        self.settings = {"separated": separate, "language": language}
        self.work = work = session_dir / "work" / rec.id
        work.mkdir(parents=True, exist_ok=True)
        recs_dir = session_dir / "recordings"
        recs_dir.mkdir(exist_ok=True)
        self.cleaned_m4a = recs_dir / f"{rec.id}_music_removed.m4a"
        self.conv_m4a = recs_dir / f"{rec.id}_conversation.m4a"

        # A different file under the same name (a re-cut sample, a replaced recording): start over.
        source, known = _fingerprint(rec), _read(work / "source.json")
        if known is not None and known != source:
            for st in STAGES:
                self._marker(st).unlink(missing_ok=True)
        (work / "source.json").write_text(json.dumps(source), encoding="utf-8")

        # Start at the first step that isn't done, or that --redo or a changed setting asks for.
        done = [_read(self._marker(st)) for st in STAGES]  # None: not done, or a damaged marker
        start = next((i for i, d in enumerate(done) if d is None), len(STAGES))
        if redo:
            start = min(start, STAGES.index(redo))
        for key, stage in SETTING_STAGE.items():  # e.g. --language changed since it was transcribed
            ran_with, now = done[STAGES.index(stage)], self.settings[key]
            if ran_with is not None and ran_with.get(key, now) != now:
                start = min(start, STAGES.index(stage))
        if start == len(STAGES) and not self._outputs_ok():
            start = STAGES.index("classify")  # finished before, but its outputs are gone
        inputs = {"separate": [work / "input_44k.wav"], "polish": [work / "vocals.wav"],
                  "transcribe": [self.cleaned_m4a], "classify": [work / "whisper.json", self.cleaned_m4a]}
        while 0 < start < len(STAGES) and not all(p.exists() for p in inputs[STAGES[start]]):
            start -= 1  # its inputs were cleaned up: rebuild them first
        self.start = start

    def _marker(self, stage):
        return self.work / f"{stage}.done"

    def _outputs_ok(self):
        info = _read(self.work / "recording.json")
        return (info is not None and self.cleaned_m4a.exists()
                and (self.conv_m4a.exists() or not info.get("sections")))

    def seconds(self):
        """Processing time of all this recording's steps so far, for the speed estimate."""
        return sum((_read(self._marker(st)) or {}).get("seconds", 0) for st in STAGES)

    def needs(self, stage):
        return STAGES.index(stage) >= self.start

    def run(self, stage, log=print):
        if not self.needs(stage):
            return
        for later in STAGES[STAGES.index(stage):]:  # so an interrupted redo can't look finished
            self._marker(later).unlink(missing_ok=True)
        log(f"     {self.rec.src.name} …", end="", flush=True)
        t = time.time()
        getattr(self, stage)(log)
        took = time.time() - t
        self._marker(stage).write_text(json.dumps({"seconds": round(took, 1), **self.settings}))
        log(f" {_fmt(took)}")
        used_up = {"separate": "input_44k.wav", "polish": "vocals.wav", "classify": "cleaned.wav"}
        if not self.keep and stage in used_up:  # big temporary WAVs go as soon as they're used
            (self.work / used_up[stage]).unlink(missing_ok=True)

    def convert(self, log):
        audio.ffmpeg("-i", str(self.rec.src), *audio.to_mono_args(self.rec.src), "-ar", str(SR),
                     "-c:a", "pcm_f32le", str(self.work / "input_44k.wav"))

    def separate(self, log):
        if self.settings["separated"]:
            _remove_music(self.rec, self.work)
        else:  # a quiet place: the converted audio goes straight on
            (self.work / "input_44k.wav").replace(self.work / "vocals.wav")

    def polish(self, log):
        (self.work / "ast.json").unlink(missing_ok=True)  # cached scores are for the old audio
        cleaned = self.work / "cleaned.wav"
        audio.ffmpeg("-i", str(self.work / "vocals.wav"), "-af",
                     "highpass=f=120,lowpass=f=7500,loudnorm=I=-16:TP=-1.5:LRA=11",
                     "-ar", str(SR), "-c:a", "pcm_s16le", str(cleaned))
        audio.ffmpeg("-i", str(cleaned), "-c:a", "aac", "-b:a", "128k", str(self.cleaned_m4a))

    def transcribe(self, log):
        _transcribe(self._cleaned(), self.work / "whisper.json", self.settings["language"], log)

    def classify(self, log):
        _find_conversations(self.rec, self.work, self._cleaned(), self.conv_m4a)

    def _cleaned(self):
        """The cleaned audio: the WAV while it's still there, else its kept m4a copy."""
        wav = self.work / "cleaned.wav"
        return wav if wav.exists() else self.cleaned_m4a

    def finish(self):
        if not self.keep:
            for name in ("input_44k.wav", "vocals.wav", "cleaned.wav"):
                (self.work / name).unlink(missing_ok=True)
        return json.loads((self.work / "recording.json").read_text(encoding="utf-8"))


def _read(path):
    """A small JSON file, or None if it's missing or damaged (e.g. an interrupted write)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _fingerprint(rec):
    """Size, length and a hash of the first and last MiB: cheap, and enough to tell files apart."""
    h, size = hashlib.sha256(), rec.src.stat().st_size
    with open(rec.src, "rb") as f:
        h.update(f.read(1 << 20))
        f.seek(max(0, size - (1 << 20)))
        h.update(f.read(1 << 20))
    return {"size": size, "duration": round(rec.duration, 2), "sha256_ends": h.hexdigest()}


def _fmt(sec):
    sec = int(round(sec))
    return f"{sec // 60}m {sec % 60:02d}s" if sec >= 60 else f"{sec}s"
