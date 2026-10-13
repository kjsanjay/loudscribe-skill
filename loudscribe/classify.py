"""Decide which Whisper segments are conversation, song lyrics, or junk, and group them."""
import difflib
import re

import numpy as np

# Hard drops: Whisper output that isn't trustworthy speech.
MAX_NO_SPEECH = 0.6      # Whisper thinks nobody is talking
MIN_AVG_LOGPROB = -1.0   # Whisper is guessing
MAX_COMPRESSION = 2.4    # Whisper is looping ("I can go! I can go! ...")
STOCK = {"thank you", "thanks for watching", "you", "bye", "so let's go"}  # its stock hallucinations

MERGE_GAP_S = 20.0       # conversation lines closer than this form one section
MIN_SECTION_WORDS = 4


def norm(text):
    return re.sub(r"[^\w' ]|_", "", text.lower()).strip()  # letters of any language, digits


def _similar(a, b, threshold):
    m = difflib.SequenceMatcher(None, a, b)
    return m.real_quick_ratio() > threshold and m.quick_ratio() > threshold and m.ratio() > threshold


def classify(segs, scorer):
    """Label each Whisper segment 'conv', 'lyric' or 'drop', with reasons.

    scorer(spans) -> [(p_speech, p_singing)] for stretches of the music-removed audio.
    """
    kept = []
    for i, s in enumerate(segs):
        s["text"] = s["text"].strip()
        s["n"] = norm(s["text"])
        why = []
        if not s["n"]:
            why.append("empty")
        if s["no_speech_prob"] > MAX_NO_SPEECH:
            why.append(f"no_speech={s['no_speech_prob']:.2f}")
        if s["avg_logprob"] < MIN_AVG_LOGPROB:
            why.append(f"logprob={s['avg_logprob']:.2f}")
        if s["compression_ratio"] > MAX_COMPRESSION:
            why.append(f"loop compr={s['compression_ratio']:.1f}")
        if s["n"] and s["n"] in [p["n"] for p in segs[max(0, i - 2):i]]:
            why.append("dup-of-prev")  # same line twice at a 30 s window boundary; first copy kept
        if s["n"] in STOCK:
            why.append("stock-hallucination")
        s["label"], s["reasons"] = ("drop" if why else "conv"), why
        if not why:
            kept.append(s)

    # Choruses come back minutes later; people repeating themselves do it within seconds.
    for s in kept:
        s["repeats"] = sum(1 for o in kept if o is not s and len(s["n"]) > 8
                           and abs(o["start"] - s["start"]) > 30 and _similar(s["n"], o["n"], 0.8))

    # An AudioSet classifier on the music-removed audio decides speech vs singing. It beat
    # text rules (rhyme, repetition) that flagged ordinary lines like "Yeah, yeah." as lyrics.
    for s, (sp, sing) in zip(kept, scorer([(s["start"], s["end"]) for s in kept])):
        s["p_speech"], s["p_sing"] = sp, sing
        chorus = s["repeats"] >= 3 and len(s["n"].split()) >= 4 and sing >= 0.05
        s["label"] = "lyric" if (sing >= 0.15 and sing > sp) or chorus else "conv"
        s["reasons"] = [f"speech {sp:.2f} / singing {sing:.2f}"] + (
            [f"chorus x{s['repeats']}"] if chorus else [])

    # Live vocals through a festival PA score weaker as singing, so use song context: a line
    # with some singing evidence that repeats a lyric or sits among lyrics is a lyric too.
    # Requiring singing >= 0.08 keeps stock phrases ("Nice to meet you.") as conversation.
    lyrics = [s for s in kept if s["label"] == "lyric"]
    for s in kept:
        if s["label"] != "conv" or s["p_sing"] < 0.08:
            continue
        near = sum(1 for l in lyrics if abs(l["start"] - s["start"]) <= 15)
        chorus = len(s["n"]) > 12 and any(abs(l["start"] - s["start"]) > 5
                                          and _similar(s["n"], l["n"], 0.75) for l in lyrics)
        if chorus or near >= 2:
            s["label"] = "lyric"
            s["reasons"].insert(1, "repeats a lyric" if chorus else f"{near} lyrics within 15 s")

    # A continuous stretch (gaps < 1.5 s) over 60 s that is mostly lyrics is a song.
    runs, cur = [], []
    for s in kept:
        if cur and s["start"] - cur[-1]["end"] > 1.5:
            runs.append(cur)
            cur = []
        cur.append(s)
    runs += [cur] if cur else []
    for r in runs:
        if r[-1]["end"] - r[0]["start"] > 60 and np.mean([s["label"] == "lyric" for s in r]) >= 0.6:
            for s in r:
                if s["label"] == "conv" and s["p_speech"] < 0.5:
                    s["label"] = "lyric"
                    s["reasons"].append("in sung run")
    return segs


def sections(segs):
    """Group conversation lines into sections; drop sections too short to matter."""
    secs = []
    for s in (s for s in segs if s["label"] == "conv"):
        if secs and s["start"] - secs[-1][-1]["end"] <= MERGE_GAP_S:
            secs[-1].append(s)
        else:
            secs.append([s])
    return [sec for sec in secs if sum(len(s["n"].split()) for s in sec) >= MIN_SECTION_WORDS]
