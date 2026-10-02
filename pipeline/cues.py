# pipeline/cues.py
"""Cue markers: word-level narration/visual sync.

script_agent writes segment text with inline markers ``[[1]]``, ``[[2]]``, ...
placed immediately before the word where a visual should land. Everything that
treats the text as prose (TTS, captions, fact check, quiz, ...) uses the clean
text; the marked text (``cue_text``) goes to manim_agent, whose scene calls
``self.cue(k)`` before the animation for marker k.

The TTS backends turn each marker into a time (seconds from the start of its
segment's audio): ElevenLabs from its character alignment, Kokoro from its
token timings, anything else proportionally by character position. A segment's
``cues`` list is in marker-number order: ``cues[k-1]`` is marker k (``None``
when the number was skipped).
"""
from __future__ import annotations

import re
from bisect import bisect_left

_MARKER = re.compile(r"\[\[\s*(\d+)\s*\]\]")


def strip_cues(text: str) -> str:
    """Text with every cue marker removed (whitespace kept natural)."""
    return parse_cues(text)[0]


def parse_cues(text: str) -> tuple[str, dict[int, int]]:
    """Remove the markers. Returns (clean_text, {cue_number: char_offset}).

    The offset points at the first non-space character after the marker in
    the clean text, i.e. the first letter of the cued word. A repeated number
    keeps its first position.
    """
    text = re.sub(r"[ \t]{2,}", " ", text or "")
    out: list[str] = []
    offsets: dict[int, int] = {}
    pos = 0
    for m in _MARKER.finditer(text):
        out.append(text[pos:m.start()])
        pos = m.end()
        built = "".join(out)
        rest = text[pos:]
        lead = len(rest) - len(rest.lstrip())
        if not built or built[-1].isspace():
            # "the [[1]] slope" -> "the slope": drop the space after the marker.
            pos += lead
            anchor = len(built)
        else:
            # "slope.[[2]] Then" -> "slope. Then": the word starts after the space.
            anchor = len(built) + lead
        offsets.setdefault(int(m.group(1)), anchor)
    out.append(text[pos:])
    clean = "".join(out)
    return clean, {k: min(v, max(0, len(clean) - 1)) for k, v in offsets.items()}


def char_time(offsets: dict[int, int], cues: list, clean_len: int, duration: float, char: int) -> float:
    """Time of character `char`, interpolated through the cue anchors
    (piecewise linear from (0, 0) to (len, duration)). Used for captions."""
    pts = sorted({(off, cues[k - 1]) for k, off in offsets.items()
                  if k - 1 < len(cues) and cues[k - 1] is not None})
    pts = [(0, 0.0)] + [p for p in pts if 0 < p[0] < clean_len] + [(max(1, clean_len), duration)]
    for (a, ta), (b, tb) in zip(pts, pts[1:]):
        if a <= char <= b:
            if tb < ta:
                break
            return ta + (tb - ta) * (char - a) / max(1, b - a)
    return duration * char / max(1, clean_len)


def segment_cue_text(seg: dict) -> str:
    """The marked text of a segment (falls back to its plain text)."""
    return seg.get("cue_text") or seg.get("text", "")


def clean_segments(segments: list[dict]) -> list[dict]:
    """Copies of segments with marker-free `text` (`cue_text` keeps the markers)."""
    out = []
    for s in segments:
        raw = segment_cue_text(s)
        clean = strip_cues(raw)
        out.append({**s, "text": clean, **({"cue_text": raw} if _MARKER.search(raw) else {})})
    return out


def _as_list(offset_times: dict[int, float | None]) -> list[float | None]:
    if not offset_times:
        return []
    n = max(offset_times)
    return [None if offset_times.get(k) is None else round(float(offset_times[k]), 3)
            for k in range(1, n + 1)]


def proportional_cue_times(clean: str, offsets: dict[int, int], duration: float) -> list[float | None]:
    """Estimate: a cue at character i of n lands at i/n of the duration."""
    n = max(1, len(clean))
    return _as_list({k: duration * off / n for k, off in offsets.items()})


def cue_times_from_alignment(clean: str, offsets: dict[int, int], characters: list[str],
                             starts: list[float], duration: float) -> list[float | None]:
    """ElevenLabs: map character offsets to the alignment's start times.

    `characters` normally equals the input text one-to-one. When it does not
    (normalized text, dropped characters) the offset is mapped through a
    sequential match of the characters, then by relative position.
    """
    if not offsets:
        return []
    if not characters or not starts or len(characters) != len(starts):
        return proportional_cue_times(clean, offsets, duration)
    if "".join(characters) == clean:
        return _as_list({k: starts[off] for k, off in offsets.items()})
    # Greedy sequential match: char index in `clean` -> alignment index.
    mapping: dict[int, int] = {}
    j = 0
    for i, ch in enumerate(clean):
        k = j
        while k < len(characters) and k < j + 8 and characters[k] != ch:
            k += 1
        if k < len(characters) and characters[k] == ch:
            mapping[i] = k
            j = k + 1
    times: dict[int, float] = {}
    for cue, off in offsets.items():
        idx = mapping.get(off)
        if idx is None:
            idx = min(len(starts) - 1, round(off * len(starts) / max(1, len(clean))))
        times[cue] = starts[idx]
    return _as_list(times)


def cue_times_from_tokens(clean: str, offsets: dict[int, int],
                          tokens: list[tuple[str, float | None]], duration: float) -> list[float | None]:
    """Kokoro: tokens are (text, start_sec) in order, start relative to the
    segment audio. Each token is located in `clean` sequentially; a cue takes
    the start of the first token at or after its offset."""
    if not offsets:
        return []
    spans: list[tuple[int, float]] = []
    cursor = 0
    for text, start in tokens:
        text = (text or "").strip()
        if not text or start is None:
            continue
        at = clean.find(text, cursor)
        if at < 0 or at - cursor > 40:
            continue
        spans.append((at, float(start)))
        cursor = at + len(text)
    if not spans:
        return proportional_cue_times(clean, offsets, duration)
    starts_at = [s for s, _ in spans]
    times: dict[int, float] = {}
    for cue, off in offsets.items():
        i = bisect_left(starts_at, off)
        if i < len(spans):
            times[cue] = spans[i][1]
        else:
            times[cue] = proportional_cue_times(clean, {cue: off}, duration)[cue - 1]
    return _as_list(times)


def caption_lines(text: str, max_chars: int = 84) -> list[tuple[int, int]]:
    """Split clean text into caption chunks: sentences, long ones at commas.

    Returns (start_char, end_char) spans covering the text in order.
    """
    spans: list[tuple[int, int]] = []
    for m in re.finditer(r"[^.!?]+[.!?]*[\"')\]]*\s*", text):
        a, b = m.start(), m.end()
        piece = text[a:b]
        while len(piece.strip()) > max_chars:
            cut = piece.rfind(", ", 0, max_chars)
            if cut <= 0:
                cut = piece.rfind(" ", 0, max_chars)
            if cut <= 0:
                break
            spans.append((a, a + cut + 1))
            a += cut + 1
            piece = text[a:b]
        if piece.strip():
            spans.append((a, b))
    return spans or [(0, len(text))]
