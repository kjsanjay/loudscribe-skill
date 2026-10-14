"""A session = one folder of recordings (a night out). Builds the combined outputs:

  only_conversations.m4a   every conversation section, in time order, 1 s apart
  transcript.txt      every conversation line, wall-clock times, where each section is in each file
  index.json          the same, for `loudscribe find` and the viewer
  viewer.html         open in a browser: search, click a line to hear it (works offline)
"""
import datetime as dt
import html
import json
from pathlib import Path

from . import audio


def clock(sec):
    """A position or length as 1:02:03, or 2:03 under an hour."""
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def duration_words(sec):
    """A rough length for people: '25 min', '2.5 hours'."""
    m = max(1, round(sec / 60))
    return f"{m} min" if m < 90 else f"{m / 60:.1f} hours".replace(".0 ", " ")


def about(lo, hi):
    """'about 25 min' or 'about 25–40 min' / 'about 2.5–3 hours' for a time range in seconds."""
    a, b = duration_words(lo), duration_words(hi)
    if a == b:
        return f"about {a}"
    if a.split()[1] == b.split()[1]:  # same unit: "25–40 min"
        return f"about {a.split()[0]}–{b}"
    return f"about {a} to {b}"


def build(session_dir, name, results):
    """results: recording.json dicts, oldest first. Returns the index dict."""
    recs_dir = session_dir / "recordings"
    combined = session_dir / "only_conversations.m4a"
    offset, recordings, sections, lines, parts = 0.0, [], [], [], []
    for r in results:
        conv = recs_dir / f"{r['id']}_only_conversations.m4a"
        recordings.append({
            "id": r["id"], "start": r["start"], "start_source": r["start_source"],
            "duration": r["duration"], "speech_seconds": r["speech_seconds"],
            "sections": len(r["sections"]), "combined_offset": offset,
            "music_removed_audio": f"recordings/{r['id']}_music_removed.m4a",
            "conversation_audio": f"recordings/{r['id']}_only_conversations.m4a" if conv.exists() else None})
        sections += [{**s, "rec": r["id"], "t_combined": round(offset + s["t_conv"], 2)}
                     for s in r["sections"]]
        lines += [{**l, "rec": r["id"], "t_combined": round(offset + l["t_conv"], 2)} for l in r["lines"]]
        if conv.exists():
            parts.append(conv)
            offset += audio.duration(conv)
    if parts:
        listing = session_dir / "work" / "concat.txt"
        listing.write_text("".join("file '{}'\n".format(p.resolve().as_posix().replace("'", "'\\''"))
                                   for p in parts), encoding="utf-8")
        audio.ffmpeg("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(combined))
    elif combined.exists():
        combined.unlink()

    index = {"session": name, "created": dt.datetime.now().isoformat(timespec="seconds"),
             "combined_audio": "only_conversations.m4a" if parts else None,
             "speech_seconds": sum(r["speech_seconds"] for r in results),
             "recordings": recordings, "sections": sections, "lines": lines}
    (session_dir / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    _write_transcripts(session_dir, index)
    _write_viewer(session_dir, index)
    return index


def _write_transcripts(session_dir, index):
    out = [f"{index['session']}: {plural(len(index['recordings']), 'recording')}, "
           f"{clock(index['speech_seconds'])} of conversation",
           "Times are wall-clock. Section headers say where the section starts in",
           "  only_conversations.m4a (combined, ▶) and in the full recording (recordings/<id>_music_removed.m4a).",
           "Find any line:  loudscribe find \"words from the line\"   or open viewer.html", ""]
    for rec in index["recordings"]:
        start = dt.datetime.fromisoformat(rec["start"])
        guess = "" if rec["start_source"] == "filename" else " (start time estimated from the file date)"
        head = (f"=== {rec['id']} · started {start:%Y-%m-%d %H:%M:%S}{guess} · "
                f"{rec['duration'] / 60:.0f} min · {plural(rec['sections'], 'section')}, "
                f"{clock(rec['speech_seconds'])} of conversation ===")
        own = [head, ""]
        out += [head, ""]
        for sec in (s for s in index["sections"] if s["rec"] == rec["id"]):
            span = f"--- {sec['wall_start']}–{sec['wall_end']}"
            out.append(f"{span} · ▶ {clock(sec['t_combined'])} · recording {clock(sec['start'])} ---")
            own.append(f"{span} · conversation file {clock(sec['t_conv'])} · "
                       f"recording {clock(sec['start'])} ---")
            body = [f"[{l['wall']}] {l['text']}" for l in index["lines"]
                    if l["rec"] == rec["id"] and l["section"] == sec["n"]]
            out += body + [""]
            own += body + [""]
        if not rec["sections"]:
            out += ["(no conversation found)", ""]
            own += ["(no conversation found)", ""]
        (session_dir / "recordings" / f"{rec['id']}_transcript.txt").write_text("\n".join(own),
                                                                             encoding="utf-8")
    (session_dir / "transcript.txt").write_text("\n".join(out), encoding="utf-8")


def _write_viewer(session_dir, index):
    template = (Path(__file__).parent / "viewer.html").read_text(encoding="utf-8")
    data = json.dumps(index).replace("<", "\\u003c")  # text can't end or confuse the <script>
    page = template.replace("__TITLE__", html.escape(index["session"])).replace("__DATA__", data)
    (session_dir / "viewer.html").write_text(page, encoding="utf-8")


def write_where(session_dir, source, index):
    """WHERE_THINGS_ARE.txt: every file this run made or used, with full paths. Returns the text."""
    from .models import HOME, hf_hub_dir
    recs = session_dir / "recordings"
    lines = [f"Where everything is: {index['session']}", "",
             "Your original recordings (only read, never changed):", f"  {source}", "",
             "Results:", f"  {session_dir}",
             f"  {session_dir / 'viewer.html'}",
             "      open it to search what was said and click a line to hear it",
             f"  {session_dir / 'transcript.txt'}",
             "      every conversation, with clock times"]
    if index["combined_audio"]:
        lines += [f"  {session_dir / index['combined_audio']}",
                  "      all the conversations joined into one file, music removed"]
    lines += ["", "Each recording:"]
    for r in index["recordings"]:
        lines += [f"  {recs / (r['id'] + '_music_removed.m4a')}   the whole recording, music removed"]
        if r["conversation_audio"]:
            lines += [f"  {recs / (r['id'] + '_only_conversations.m4a')}   just its conversations"]
        lines += [f"  {recs / (r['id'] + '_transcript.txt')}   its transcript"]
    lines += ["", "Logs and technical files:",
              f"  {session_dir / 'progress.log'}", "      what happened during processing, with timings",
              f"  {session_dir / 'index.json'}",
              "      the transcript as data (for `loudscribe find` and the viewer)",
              f"  {session_dir / 'work'}",
              "      per recording: segments.tsv (why each line was kept or dropped) and the raw transcription",
              "", "Tools and models (shared by all nights):",
              f"  {HOME}", "      the Python environment, the music-removal model and temporary files",
              f"  {hf_hub_dir()}", "      the speech-recognition and speech-vs-singing models", ""]
    text = "\n".join(lines)
    (session_dir / "WHERE_THINGS_ARE.txt").write_text(text, encoding="utf-8")
    return text
