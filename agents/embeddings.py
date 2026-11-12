"""Idea de-duplication without an external embedding API.

A hashed bag-of-ngrams vector (256 dims) is deterministic, free, offline and good enough to catch
near-duplicate hypotheses ("momentum in sector ETFs" vs "sector ETF momentum"). The interface is
the same shape a real embedding model would expose, so swapping it later touches one function.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable

DIMENSIONS = 256
_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    words = _WORD.findall(text.lower())
    grams = [f"w:{w}" for w in words]
    grams += [f"b:{words[i]}_{words[i + 1]}" for i in range(len(words) - 1)]
    chars = re.sub(r"\s+", " ", text.lower())
    grams += [f"c:{chars[i:i + 4]}" for i in range(max(0, len(chars) - 3))]
    return grams


def embed(text: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    for token in _tokens(text):
        digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % DIMENSIONS
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(v * v for v in vector))
    if norm <= 0:
        return vector
    return [v / norm for v in vector]


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    va, vb = list(a), list(b)
    if len(va) != len(vb) or not va:
        return 0.0
    dot = sum(x * y for x, y in zip(va, vb, strict=True))
    na = math.sqrt(sum(x * x for x in va))
    nb = math.sqrt(sum(y * y for y in vb))
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


def best_match(text: str, existing: list[tuple[str, list[float]]]) -> tuple[str, float] | None:
    if not existing:
        return None
    vector = embed(text)
    scored = [(key, cosine(vector, other)) for key, other in existing if other]
    if not scored:
        return None
    key, score = max(scored, key=lambda kv: kv[1])
    return key, score


def is_duplicate(text: str, existing: list[tuple[str, list[float]]], threshold: float = 0.86) -> tuple[bool, str | None, float]:
    match = best_match(text, existing)
    if match is None:
        return False, None, 0.0
    key, score = match
    return score >= threshold, key, score
