"""Speech-vs-singing scores for stretches of a vocals track (AudioSet AST classifier).

Conversation left after music removal scores as Speech/Conversation; song lyrics as
Singing/Rapping/Song. Runs on the CPU: it only scores the segments Whisper found.
"""
import json

import numpy as np

from . import audio
from .models import AST_REPO

SPEECH = [0, 1, 2, 3, 4, 5, 8, 11]                        # Speech .. Conversation, Narration, Shout, Yell
SINGING = [27, 28, 30, 32, 33, 34, 35, 36, 37, 254, 266]  # Singing .. Rapping, Humming, Vocal music, Song
SR = 16000

_model = _fe = None


def score(samples, spans, pad=0.25):
    """samples: 16 kHz mono array; spans: [(start_s, end_s)] -> [(p_speech, p_singing)]."""
    global _model, _fe
    import torch
    from transformers import ASTFeatureExtractor, ASTForAudioClassification
    if _model is None:
        _fe = ASTFeatureExtractor.from_pretrained(AST_REPO)
        _model = ASTForAudioClassification.from_pretrained(AST_REPO).eval()
    clips = []
    for a, b in spans:
        x = samples[int(max(0, a - pad) * SR): int((b + pad) * SR)]
        if len(x) < SR:  # the classifier needs some context; pad short bits to 1 s
            x = np.pad(x, (0, SR - len(x)))
        clips.append(x[: int(10.24 * SR)])
    out = []
    for i in range(0, len(clips), 16):
        feats = _fe(clips[i:i + 16], sampling_rate=SR, return_tensors="pt")
        with torch.no_grad():
            p = torch.sigmoid(_model(**feats).logits).numpy()
        out += [(float(r[SPEECH].max()), float(r[SINGING].max())) for r in p]
    return out


def cached_scorer(audio_path, cache_path):
    """A scorer(spans) that remembers results in cache_path, so re-classifying is instant."""
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    samples = None

    def scorer(spans):
        nonlocal samples
        keys = [f"{a:.2f}-{b:.2f}" for a, b in spans]
        todo = [(k, sp) for k, sp in zip(keys, spans) if k not in cache]
        if todo:
            if samples is None:
                samples = audio.load_mono(audio_path, SR)
            for (k, _), r in zip(todo, score(samples, [sp for _, sp in todo])):
                cache[k] = r
            cache_path.write_text(json.dumps(cache), encoding="utf-8")
        return [tuple(cache[k]) for k in keys]
    return scorer
