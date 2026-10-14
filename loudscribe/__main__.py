"""loudscribe: transcribe conversations from recordings made in loud places (clubs, festivals).

  loudscribe check FOLDER                  length, clipping and time estimates for a night
  loudscribe sample FOLDER [--at 23:40]    cut a short stretch to try first
  loudscribe process FOLDER [FOLDER ...]   one folder per night; see README.md
  loudscribe find "words"                  where was that said?
  loudscribe doctor                        check the install
  loudscribe setup                         download the models (once, after installing)
"""
import argparse
import json
import os
import shutil
import sys
import time
import traceback
import warnings
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):  # Windows consoles/pipes may not be UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
warnings.filterwarnings("ignore")

from . import models  # noqa: E402

DEFAULT_OUT = Path.home() / "loudscribe-output"
CLOUD_DIRS = {"dropbox", "google drive", "my drive", "box", "icloud drive"}  # folder names, any case


def _in_cloud(path):
    """True if path is inside a folder that a sync client uploads. A partial guard: the agent is
    also told to keep outputs local."""
    parts = [x.lower() for x in path.parts]
    joined = "/".join(parts)
    if "library/cloudstorage" in joined or "library/mobile documents" in joined:  # macOS clients, iCloud
        return True
    if any(x in CLOUD_DIRS or x.startswith("onedrive") for x in parts):
        return True
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):  # Windows sync roots
        root = os.environ.get(var)
        if root:
            try:
                path.relative_to(Path(root).resolve())
                return True
            except ValueError:
                pass
    return False


def _session_dir(out, folder):
    """out/<folder name>, or out/<folder name>-2 if that name is taken by a different folder."""
    from . import session as S
    for n in range(1, 100):
        d = out / (folder.name if n == 1 else f"{folder.name}-{n}")
        meta = next((m for m in (d / S.TECH / "session.json", d / "session.json") if m.exists()), None)
        try:
            owner = json.loads(meta.read_text(encoding="utf-8"))["source"] if meta else None
        except (OSError, ValueError, KeyError):
            continue  # damaged: leave that folder alone
        if owner in (None, str(folder)):
            if owner:  # our results from an older version: move them into today's layout
                S.migrate(d)
            _write_session(d, folder)
            return d
    raise SystemExit(f"Too many output folders named {folder.name}")


def _write_session(sdir, folder):
    from .session import TECH
    (sdir / TECH).mkdir(parents=True, exist_ok=True)
    (sdir / TECH / "session.json").write_text(json.dumps({"source": str(folder)}), encoding="utf-8")


def _keep_awake():
    """Ask Windows not to sleep (it sleeps after its idle timeout, often 1 h even plugged in, which
    pauses the run) and not to confine this process to the efficiency cores (Windows 11 power
    throttling can halve music-removal speed on a laptop). Both end with the process. Windows can
    still refuse, so say so if it does."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.SetThreadExecutionState.argtypes, k.SetThreadExecutionState.restype = [wintypes.DWORD], wintypes.DWORD
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k.SetProcessInformation.restype = wintypes.BOOL
    if not k.SetThreadExecutionState(0x80000001):  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        print("Note: Windows didn't accept the request to stay awake. Set Sleep to Never while plugged "
              "in, or the run will pause when the computer sleeps.")
    state = (wintypes.ULONG * 3)(1, 0x1, 0)  # version 1; EXECUTION_SPEED controlled, and off
    if not k.SetProcessInformation(k.GetCurrentProcess(), 4, ctypes.byref(state), ctypes.sizeof(state)):
        print(f"Note: couldn't opt out of Windows power throttling (error {ctypes.get_last_error()}); "
              f"the run may be slower when nobody is using the computer.")


def _refuse_cloud(out, allowed=False):
    if _in_cloud(out) and not allowed:
        sys.exit(f"{out} is inside a cloud-synced folder, so outputs would be uploaded.\n"
                 f"Choose a local folder with --out.")


def cmd_process(args):
    """Exit status: 0 all done, 2 some recordings failed (named at the end), 1 nothing processed."""
    from . import prep
    from . import process as P
    from . import session as S
    out = args.out.expanduser().resolve()
    _refuse_cloud(out, args.allow_cloud_output)
    folders = [f.expanduser().resolve() for f in args.folders]
    for folder in folders:
        if not folder.is_dir():
            sys.exit(f"Not a folder: {folder}")
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        sys.exit("ffmpeg isn't installed (or isn't on the PATH). Run `python -m loudscribe doctor`.")
    if not models.all_cached(music_removal=not args.no_music_removal):
        sys.exit("The models aren't downloaded yet. Run `python -m loudscribe setup` first (see SKILL.md).")
    _keep_awake()
    os.environ["HF_HUB_OFFLINE"] = "1"  # everything is local now; don't contact the model hub
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    os.environ["TQDM_DISABLE"] = "1"  # the separator's own progress bars garble ours
    engine = models.backend()
    print(f"Engine: {models.LABEL[engine]}")
    ram = models.ram_gb()
    if ram < 15 and not args.no_music_removal:
        print(f"Note: this computer has {ram:.0f} GB of memory and music removal uses about 7 GB, so "
              f"close other apps; it will run slower than the estimate.")

    status = 0
    for folder in folders:
        sample = prep.as_sample(folder)  # a sample's results go in its own folder, beside its audio
        if sample:
            folder, sdir, sample = sample
        recs, skipped = P.find_recordings(folder)
        sizes = {r.src: r.src.stat().st_size for r in recs}
        time.sleep(2)
        copying = [p.name for p, s in sizes.items() if p.stat().st_size != s]
        if copying:
            sys.exit(f"Still being copied: {', '.join(copying)}. Wait for the copy to finish, then re-run.")
        if not recs:
            sys.exit(f"No readable audio files in {folder}"
                     + (f" (couldn't read: {', '.join(skipped)})" if skipped else ""))
        if sample:
            _write_session(sdir, folder)
        else:
            sdir = _session_dir(out, folder)
        busy = (f"Another loudscribe run is already processing {sdir.name}. Wait for it to finish "
                f"({S.TECH}/progress.log shows how far it is), or stop it first.")
        with S.run_lock(sdir, busy), open(sdir / S.TECH / "progress.log", "a", encoding="utf-8") as logf:
            def log(msg="", end="\n", flush=True):
                print(msg, end=end, flush=flush)
                logf.write(msg + end)
                logf.flush()

            status = max(status, _process_folder(args, engine, sdir, recs, skipped, log, sample))
    sys.exit(status)


def _process_folder(args, engine, sdir, recs, skipped, log, sample=None):
    """Returns 0 if every recording finished, 2 if some failed, 1 if none did."""
    from . import audio
    from . import process as P
    from . import session as S
    music, failed = not args.no_music_removal, []

    def fail(name, e):  # one bad file or model crash shouldn't stop the night; keep the details
        if isinstance(e, audio.Error):
            log(f" failed: {e}")
        else:
            log(f" failed: {type(e).__name__}: {e}")
            log(traceback.format_exc().rstrip())
        failed.append(name)

    total = sum(r.duration for r in recs)
    log(f"\n{sdir.name}: {S.plural(len(recs), 'recording')}, {S.clock(total)} of audio  →  {sdir}")
    if skipped:
        log(f"  skipped (couldn't read, or under 1 s): {', '.join(skipped)}")
    jobs = []
    for rec in recs:
        log(f"  {rec.src.name} · {S.clock(rec.duration)} · started {rec.start:%Y-%m-%d %H:%M:%S}"
            + ("" if rec.start_source == "filename" else " (from the file date)"), end="")
        try:
            jobs.append(P.Job(rec, sdir, separate=music, language=args.language,
                              redo=args.redo, keep=args.keep_intermediates))
            log("")
        except Exception as e:
            fail(rec.src.name, e)
    todo_s = sum(j.rec.duration for j in jobs if j.start < len(P.STAGES))
    if todo_s:
        lo, hi, measured = models.speed(engine, music)
        log(f"  estimated time: {S.about(todo_s * lo + 60, todo_s * hi + 60)} "
            + ("(from this computer's last run" if measured else "(depends on the computer")
            + "; it's fine to leave it running)")

    # One step at a time across all recordings, so only one big model is loaded at once.
    t0, ran = time.time(), False
    for n, stage in enumerate(P.STAGES, 1):
        todo = [j for j in jobs if j.needs(stage)]
        if not todo:
            continue
        ran = True
        log(f"\n  step {n}/{len(P.STAGES)}: {P.STAGE_LABELS[stage]}"
            + (" (the slow part)" if stage == "separate" and music else ""))
        t = time.time()
        for job in todo:
            try:
                job.run(stage, log)
            except Exception as e:
                fail(job.rec.src.name, e)
                jobs.remove(job)
        P.release(stage)
        if stage == "separate" and music:
            took = time.time() - t
            log(f"     slow part done; {S.about(took * 0.5, took * 0.9)} left")
    if not ran and not failed:
        log("\n  already processed with these settings (--redo STEP redoes a step)")

    results, done = [], []
    for job in jobs:
        try:
            results.append(job.finish())
            done.append(job)
        except Exception as e:
            log(f"     {job.rec.src.name} …", end="")
            fail(job.rec.src.name, e)
    if not done:
        log(f"\nNothing could be processed: {', '.join(failed)} failed (see the errors above).")
        return 1
    log("")
    for job, res in zip(done, results):
        log(f"  {job.rec.src.name}: {S.plural(len(res['sections']), 'conversation section')}, "
            f"{S.clock(res['speech_seconds'])} of talking")
    index = S.build(sdir, sdir.name, results)
    audio_s = sum(j.rec.duration for j in done)
    if ran and audio_s >= 180:  # remember this computer's speed for the next estimate
        models.remember_speed(engine, music, sum(j.seconds() for j in done) / audio_s)
    log(f"\nDone in {S.duration_words(time.time() - t0)}: {S.plural(len(index['sections']), 'section')}, "
        f"{S.clock(index['speech_seconds'])} of talking"
        + (f" ({S.COMBINED} is {S.clock(index['combined_seconds'])} long, with the pauses)."
           if index["combined_audio"] else ". No conversation found, so there's no combined audio."))
    if failed:
        log(f"FAILED, not included: {', '.join(failed)} (see the errors above)")
    log("\n" + S.write_where(sdir, recs[0].src.parent, index, sample))
    return 2 if failed else 0


def main():
    from . import __version__
    ap = argparse.ArgumentParser(prog="loudscribe", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"loudscribe {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("process", help="transcribe one or more folders of recordings")
    p.add_argument("folders", nargs="+", type=Path, help="one folder per night/session")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"where results go (default {DEFAULT_OUT})")
    p.add_argument("--no-music-removal", action="store_true",
                   help="for quiet places (a class, a café): skip music removal")
    p.add_argument("--language", default="en", help="spoken language code (default en)")
    p.add_argument("--redo", choices=["separate", "polish", "transcribe", "classify"],
                   help="redo this step and the ones after it")
    p.add_argument("--keep-intermediates", action="store_true",
                   help="keep the large temporary WAV files (about 1.5 GB per hour)")
    p.add_argument("--allow-cloud-output", action="store_true", help=argparse.SUPPRESS)

    c = sub.add_parser("check", help="length, clipping and time estimates for a folder of recordings")
    c.add_argument("folder", type=Path)
    c.add_argument("--no-music-removal", action="store_true", help="estimate for a quiet place")
    c.add_argument("--json", action="store_true", help="machine-readable output")

    sm = sub.add_parser("sample", help="copy a short stretch of a night to try first")
    sm.add_argument("folder", type=Path)
    sm.add_argument("--at", help="clock time to start from, 24-hour, e.g. 23:40 (default: the start)")
    sm.add_argument("--minutes", type=float, help="length (default 10, or 5 on CPU-only computers)")
    sm.add_argument("--out", type=Path, default=DEFAULT_OUT)
    sm.add_argument("--no-music-removal", action="store_true", help="estimate for a quiet place")

    f = sub.add_parser("find", help="find where something was said")
    f.add_argument("text")
    f.add_argument("--out", type=Path, default=DEFAULT_OUT)
    f.add_argument("--session", help="only search this session (output folder name)")
    f.add_argument("--context", type=int, default=1, help="lines of context around each hit")
    f.add_argument("--json", action="store_true", help="machine-readable output")

    d = sub.add_parser("doctor", help="check the install")
    d.add_argument("--no-music-removal", action="store_true", help="music removal isn't needed")
    st = sub.add_parser("setup", help="download the models (about 3 GB)")
    st.add_argument("--no-music-removal", action="store_true", help="skip the music-removal model")

    args = ap.parse_args()
    if args.cmd == "process":
        cmd_process(args)
    elif args.cmd in ("check", "sample"):
        from . import prep
        folder = args.folder.expanduser().resolve()
        if not folder.is_dir():
            sys.exit(f"Not a folder: {folder}")
        if prep.as_sample(folder):  # its audio, not its results
            folder = prep.as_sample(folder)[0]
        if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
            sys.exit("ffmpeg isn't installed (or isn't on the PATH). Run `python -m loudscribe doctor`.")
        if args.cmd == "check":
            prep.check(folder, music=not args.no_music_removal, as_json=args.json)
        else:
            out = args.out.expanduser().resolve()
            _refuse_cloud(out)
            prep.sample(folder, out, args.at, args.minutes, music=not args.no_music_removal)
    elif args.cmd == "find":
        from .search import find
        find(args.text, args.out.expanduser(), args.session, args.context, args.json)
    elif args.cmd == "doctor":
        sys.exit(0 if models.doctor(music_removal=not args.no_music_removal) else 1)
    elif args.cmd == "setup":
        models.download(music_removal=not args.no_music_removal)


if __name__ == "__main__":
    main()
