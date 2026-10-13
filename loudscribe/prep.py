"""Before processing: `loudscribe check` (length, clipping, time estimates) and `loudscribe sample`
(cut a short stretch of a night to try first, keeping its clock times)."""
import datetime as dt
import json
import shutil
import subprocess

import numpy as np

from . import audio, models
from .process import find_recordings
from .session import about, clock, plural

CLIP_LEVEL = 32700     # 16-bit samples at or above this count as clipped (full scale is 32767)
HEAVY_CLIP = 0.05      # above ~5% of samples clipped, expect little usable speech
OVERHEAD_S = 60        # loading the models, whatever the length


def sample_minutes(engine):
    return 5 if engine == "cpu" else 10  # a 10-minute sample takes ~30 min on a laptop CPU


def clipping(path, block_s=300):
    """Share of samples at full scale, on the worst channel: overall, and in the worst 5-minute
    block. Raises audio.Error if the file can't be decoded."""
    rate, ch = audio.sample_rate(path), audio.channels(path)
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-c:a", "pcm_s16le", "-f", "s16le", "-"]
    clipped, total, worst = np.zeros(ch), 0, None
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as p:
        while buf := p.stdout.read(rate * block_s * 2 * ch):
            x = np.frombuffer(buf[:len(buf) // (2 * ch) * 2 * ch], dtype="<i2").reshape(-1, ch)
            n = np.count_nonzero(np.abs(x.astype(np.int32)) >= CLIP_LEVEL, axis=0)
            clipped, total = clipped + n, total + len(x)
            if len(x) >= rate * 60:  # a short tail block says little
                worst = max(worst or 0.0, float(n.max()) / len(x))
        err = p.stderr.read().decode("utf-8", "replace").strip()
    if p.returncode or not total:
        raise audio.Error(audio.first_error(err.splitlines()) or "couldn't decode any audio")
    overall = float(clipped.max()) / total
    return overall, overall if worst is None else worst


def _sample_source(recs, seconds):
    """Default stretch for a sample: the first recording long enough, else the longest."""
    return next((r for r in recs if r.duration >= seconds), max(recs, key=lambda r: r.duration))


def check(folder, music=True, as_json=False):
    recs, skipped = find_recordings(folder)
    if not recs:
        raise SystemExit(f"No readable audio files in {folder}")
    engine = models.backend()
    lo, hi, measured = models.speed(engine, music)
    rows = []
    for r in recs:
        try:
            overall, worst = clipping(r.src)
            verdict = ("heavy clipping" if overall > HEAVY_CLIP else
                       "some clipped stretches" if worst > HEAVY_CLIP else "fine")
        except audio.Error as e:
            overall = worst = None
            verdict = f"couldn't read: {e}"
        rows.append({"file": r.src.name, "start": r.start.isoformat(sep=" "),
                     "start_from": r.start_source, "seconds": round(r.duration, 2),
                     "clipped": None if overall is None else round(overall, 4),
                     "clipped_worst_5min": None if worst is None else round(worst, 4),
                     "verdict": verdict})
    total = sum(r.duration for r in recs)
    want = sample_minutes(engine) * 60
    sample_s = min(want, _sample_source(recs, want).duration)
    est = {"engine": engine, "measured_on_this_computer": measured, "music_removal": music,
           "night": [round(total * lo + OVERHEAD_S), round(total * hi + OVERHEAD_S)],
           "sample_minutes": round(sample_s / 60, 1),
           "sample": [round(sample_s * lo + OVERHEAD_S), round(sample_s * hi + OVERHEAD_S)]}
    if as_json:
        print(json.dumps({"folder": str(folder), "recordings": rows, "skipped": skipped,
                          "total_seconds": round(total, 2), "estimate_seconds": est}, indent=1))
        return
    print(f"{folder.name}: {plural(len(recs), 'recording')}, {clock(total)} of audio")
    for row, r in zip(rows, recs):
        clip = ("" if row["clipped"] is None else f" · clipped {100 * row['clipped']:.1f}%"
                f" (worst 5 min {100 * row['clipped_worst_5min']:.1f}%)")
        print(f"  {row['file']} · started {r.start:%Y-%m-%d %H:%M}"
              + ("" if r.start_source == "filename" else " (from the file date)")
              + f" · {clock(r.duration)}{clip} · {row['verdict']}")
    if skipped:
        print(f"  skipped (couldn't read, or under 1 s): {', '.join(skipped)}")
    if any(row["verdict"] in ("heavy clipping", "some clipped stretches") for row in rows):
        print("  Clipping over ~5% means the recorder gain was too high there: expect little conversation\n"
              "  from those stretches. RECORDER_SETUP.md (in the skill folder) says how to avoid it next time.")
    basis = ("from this computer's last run" if measured else
             f"typical for this kind of computer: {models.LABEL[engine].split(':')[0]}")
    print(f"Estimated time ({basis}):")
    print(f"  the whole night: {about(*est['night'])}")
    print(f"  a {duration_label(sample_s)} sample: {about(*est['sample'])}")


def duration_label(sec):
    return f"{round(sec / 60)}-minute" if sec >= 60 else f"{round(sec)}-second"


def _parse_clock(text):
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return dt.datetime.strptime(text, fmt).time()
        except ValueError:
            pass
    raise SystemExit(f"Can't read the time '{text}': use 24-hour HH:MM, e.g. 23:40 or 00:15")


def _sample_dir(out_root, folder):
    """out_root/samples/<night>-sample, or -sample-2 … if that one belongs to a different folder.
    A folder without our sample.json is never reused (it isn't ours to delete)."""
    for n in range(1, 100):
        d = out_root / "samples" / (f"{folder.name}-sample" + ("" if n == 1 else f"-{n}"))
        if not d.exists():
            return d
        try:
            if json.loads((d / "sample.json").read_text(encoding="utf-8"))["source"] == str(folder):
                shutil.rmtree(d)  # our own earlier sample of this night
                return d
        except (OSError, ValueError, KeyError):
            pass
    raise SystemExit(f"Too many sample folders for {folder.name} in {out_root / 'samples'}")


def sample(folder, out_root, at=None, minutes=None, music=True, default_out=True):
    """Copy a short stretch into out_root/samples/<night>-sample/, named after its own start time so
    the transcript's clock times stay right. The original is only read."""
    recs, _ = find_recordings(folder)
    if not recs:
        raise SystemExit(f"No readable audio files in {folder}")
    if minutes is not None and minutes <= 0:
        raise SystemExit("--minutes must be more than 0")
    engine = models.backend()
    want = (minutes or sample_minutes(engine)) * 60
    rec, offset = _sample_source(recs, want), 0.0
    if at:
        t, found = _parse_clock(at), False
        for r in recs:
            for day in (r.start.date(), r.start.date() + dt.timedelta(days=1)):  # nights pass midnight
                when = dt.datetime.combine(day, t)
                if r.start <= when < r.start + dt.timedelta(seconds=r.duration):
                    rec, offset, found = r, (when - r.start).total_seconds(), True
                    break
            if found:
                break
        if not found:
            spans = ", ".join(f"{r.start:%H:%M}–{r.start + dt.timedelta(seconds=r.duration):%H:%M}"
                              for r in recs)
            raise SystemExit(f"No recording covers {at}. The recordings run {spans}.")
    length = min(want, rec.duration - offset)
    start = rec.start + dt.timedelta(seconds=offset)
    dest = _sample_dir(out_root, folder)
    dest.mkdir(parents=True)
    (dest / "sample.json").write_text(json.dumps({"source": str(folder), "file": rec.src.name,
                                                  "offset": offset, "seconds": length}), encoding="utf-8")
    out = dest / f"{start:%Y%m%d%H%M%S}{rec.src.suffix}"
    audio.ffmpeg("-ss", f"{offset:.3f}", "-i", str(rec.src), "-t", f"{length:.3f}", "-c", "copy", str(out))
    lo, hi, measured = models.speed(engine, music)
    flags = ("" if default_out else f' --out "{out_root}"') + ("" if music else " --no-music-removal")
    print(f"Sample: {duration_label(length)} from {start:%H:%M:%S} "
          f"({rec.src.name}, {clock(offset)} in), copied to\n  {out}")
    print(f"Process it with:  loudscribe process \"{dest}\"{flags}")
    print(f"Estimated time: {about(length * lo + OVERHEAD_S, length * hi + OVERHEAD_S)}"
          + (" (from this computer's last run)" if measured else ""))
    return dest
