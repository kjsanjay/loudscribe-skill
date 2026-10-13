"""`loudscribe find`: where was something said? Searches every processed session's index.json."""
import datetime as dt
import difflib
import json
import re

from .session import clock


def norm(text):
    """Lowercase words; punctuation and hyphens become spaces ("after-party" -> "after party")."""
    return re.sub(r"[^\w']+|_", " ", text.lower()).strip()


def _closeness(key, line):
    """Best similarity between the query and any same-length run of words in the line, so
    "see you at the warehouse" still finds "...see you at the where house" (what a transcript heard)."""
    q, words = key.split(), line.split()
    best = difflib.SequenceMatcher(None, key, line).ratio()
    for size in {max(1, len(q) - 1), len(q), len(q) + 1}:
        for i in range(max(1, len(words) - size + 1)):
            best = max(best, difflib.SequenceMatcher(None, key, " ".join(words[i:i + size])).ratio())
    return best


def find(query, out_root, session=None, context=1, as_json=False, limit=20):
    key = norm(query)
    paths = sorted(out_root.glob("*/index.json"))
    if session:
        paths = [p for p in paths if p.parent.name == session]
    if not paths:
        print(f"No processed sessions in {out_root}" + (f" named '{session}'" if session else ""))
        return []
    lines = []  # (session folder, its index, one line)
    for p in paths:
        idx = json.loads(p.read_text(encoding="utf-8"))
        lines += [(p.parent, idx, line) for line in idx["lines"]]
    hits = [(1.0, *x) for x in lines if key and key in norm(x[2]["text"])]
    fuzzy = not hits
    if fuzzy and key:  # transcripts mishear words: fall back to the closest-sounding lines
        hits = sorted(((_closeness(key, norm(x[2]["text"])), *x) for x in lines), key=lambda h: -h[0])
        hits = [h for h in hits if h[0] >= 0.7]

    results = []
    for score, folder, idx, line in hits[:limit]:
        rec = next(r for r in idx["recordings"] if r["id"] == line["rec"])
        section = [x for x in idx["lines"] if x["rec"] == line["rec"] and x["section"] == line["section"]]
        j = section.index(line)
        when = dt.datetime.fromisoformat(rec["start"]) + dt.timedelta(seconds=line["t"])
        results.append({
            "session": idx["session"], "folder": str(folder), "recording": line["rec"],
            "wall": line["wall"], "date": when.date().isoformat(),
            "text": line["text"], "match": round(score, 2),
            "context_before": [x["text"] for x in section[max(0, j - context):j]],
            "context_after": [x["text"] for x in section[j + 1:j + 1 + context]],
            "conversations_m4a": line["t_combined"], "conversation_file": line["t_conv"],
            "recording_m4a": line["t"],
            "files": {"combined": str(folder / "conversations.m4a"),
                      "conversation": str(folder / rec["conversation_audio"]),
                      "recording": str(folder / rec["music_removed_audio"])}})
    if as_json:
        print(json.dumps(results, indent=1))
        return results
    if not results:
        print(f'Nothing found for "{query}".')
    elif fuzzy:
        print(f'No exact match for "{query}"; closest lines:\n')
    for r in results:
        print(f"{r['session']} · {r['date']} {r['wall']} · recording {r['recording']}"
              + (f" · {int(r['match'] * 100)}% match" if fuzzy else ""))
        for c in r["context_before"]:
            print(f"      {c}")
        print(f"   ▶  {r['text']}")
        for c in r["context_after"]:
            print(f"      {c}")
        print(f"   conversations.m4a {clock(r['conversations_m4a'])}  ·  "
              f"{r['recording']}_conversation.m4a {clock(r['conversation_file'])}  ·  "
              f"full recording {clock(r['recording_m4a'])}\n")
    if len(hits) > limit:
        print(f"(showing {limit} of {len(hits)}; use more words to narrow it down)")
    return results
