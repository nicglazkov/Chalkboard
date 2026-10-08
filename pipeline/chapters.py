# pipeline/chapters.py
"""Chapters: the list embedded in final.mp4 (chapters.txt), shown on the video
page and returned by the timeline API.

The script agent marks chapters inline with ``[[ch: Short title]]``: one at the
start of every segment and one right before the cue marker of every distinct
step, item or idea the visuals present (``[[ch: 3. Region test]] [[2]] Number
three: ...``). A chapter tied to a cue starts when that cue's word is spoken,
which is when its visual lands (``self.cue(k)`` holds the scene until then).
Times come from ``segments.json``, whose cue times and anchors already include
the pauses inserted by pipeline/pacing.py, so chapters stay on the final,
paced timeline.

Runs written before chapter markers existed get one chapter per segment with
the first words of the segment as the title: exactly what their chapters.txt
already holds. Nothing finer is invented for them.
"""
from __future__ import annotations

import re

from pipeline.cues import (
    _CHAPTER, anchor_time, char_time, parse_chapter_markers, parse_cues, segment_cue_text,
)

# Chapters closer together than this are merged (the later one is dropped),
# except that two numbered steps are never merged away.
MIN_GAP_S = 4.0
LEGACY_TITLE_CHARS = 60
MAX_TITLE_CHARS = 60

_NUMBERED = re.compile(
    r"^\s*(?:(?:step|part|no\.?|number|tip|rule|stage|phase|item|formula|level|layer|day|week)\s*#?\d+\b"
    r"|#?\d+\s*[.):])",
    re.IGNORECASE,
)


def is_numbered(title: str) -> bool:
    """True for a numbered step/item title: "3. Region test", "Step 3: ...", "#3) ..."."""
    return bool(_NUMBERED.match(title or ""))


def legacy_title(text: str) -> str:
    """The pre-0.5 chapter title: the segment's first 60 characters."""
    raw = text or ""
    return (raw[:LEGACY_TITLE_CHARS].rstrip() + "...") if len(raw) > LEGACY_TITLE_CHARS else raw


def has_chapter_markers(segments: list[dict]) -> bool:
    return any(_CHAPTER.search(segment_cue_text(s) or "") for s in segments or [])


def _clean_title(title: str) -> str:
    title = " ".join((title or "").split())
    if len(title) > MAX_TITLE_CHARS:
        title = title[:MAX_TITLE_CHARS - 1].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
    return title


def _marker_time(seg: dict, clean: str, offset: int, cue: int | None) -> float:
    """Seconds from the segment start at which a chapter marker begins."""
    cues = seg.get("cues") or []
    dur = float(seg.get("actual_duration_sec") or 0.0)
    if cue is not None and 0 < cue <= len(cues) and cues[cue - 1] is not None:
        return float(cues[cue - 1])
    if not clean[:offset].strip():
        return 0.0
    t = anchor_time(seg.get("anchors"), offset)
    if t is not None:
        return t
    _, offsets = parse_cues(segment_cue_text(seg))
    return char_time(offsets, cues, len(clean), dur, offset)


def _space(chapters: list[dict], min_gap: float) -> list[dict]:
    """Drop repeats and chapters closer than `min_gap` to the previous one.
    Numbered steps are never dropped for spacing: a numbered chapter replaces
    an unnumbered one just before it, and two numbered ones both stay."""
    kept: list[dict] = []
    for c in chapters:
        if kept and c["title"].casefold() == kept[-1]["title"].casefold():
            continue
        if kept and c["start_s"] - kept[-1]["start_s"] < min_gap:
            prev = kept[-1]
            if c["numbered"] and prev["numbered"]:
                kept.append(c)
            elif c["numbered"]:
                kept[-1] = c
            continue
        kept.append(c)
    return kept


def build_chapters(segments: list[dict], *, min_gap: float = MIN_GAP_S) -> list[dict]:
    """[{"index", "start_s", "end_s", "title", "segment", "cue", "numbered", "source"}].

    `source` is "script" for chapters from [[ch: ...]] markers and "segments"
    for the one-per-segment chapters of older runs. Empty when a segment has
    no measured duration (TTS has not run)."""
    if not segments:
        return []
    durs = []
    for s in segments:
        d = s.get("actual_duration_sec")
        if not isinstance(d, (int, float)):
            return []
        durs.append(float(d))
    starts, acc = [], 0.0
    for d in durs:
        starts.append(acc)
        acc += d
    total = acc

    chapters: list[dict] = []
    if not has_chapter_markers(segments):
        for i, seg in enumerate(segments):
            chapters.append({"start_s": starts[i], "title": legacy_title(seg.get("text", "")),
                             "segment": i, "cue": None, "numbered": False, "source": "segments"})
    else:
        last = 0.0
        for i, seg in enumerate(segments):
            marked = segment_cue_text(seg)
            clean = parse_cues(marked)[0]
            for off, title, cue in parse_chapter_markers(marked):
                title = _clean_title(title)
                if not title:
                    continue
                t = starts[i] + min(max(0.0, _marker_time(seg, clean, off, cue)), durs[i])
                t = max(t, last)  # never out of order
                last = t
                chapters.append({"start_s": t, "title": title, "segment": i, "cue": cue,
                                 "numbered": is_numbered(title), "source": "script"})
        chapters = _space(chapters, min_gap)
        if chapters:
            # The chapter list starts at 0:00 (players and YouTube expect it); the
            # script agent opens segment 1 with a chapter, so this only moves a
            # first chapter that came a moment late.
            chapters[0]["start_s"] = 0.0

    for j, c in enumerate(chapters):
        c["index"] = j
        c["end_s"] = chapters[j + 1]["start_s"] if j + 1 < len(chapters) else total
        c["start_s"] = round(c["start_s"], 3)
        c["end_s"] = round(c["end_s"], 3)
    return [{k: c[k] for k in ("index", "start_s", "end_s", "title", "segment", "cue", "numbered", "source")}
            for c in chapters]


def _ffmeta_escape(value: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", value)


def ffmetadata(chapters: list[dict]) -> str:
    """FFMETADATA1 text for ffmpeg (`-f ffmetadata -i chapters.txt`)."""
    lines = [";FFMETADATA1\n"]
    for c in chapters:
        start = int(round(c["start_s"] * 1000))
        end = max(start, int(round(c["end_s"] * 1000)))
        lines.append(f"\n[CHAPTER]\nTIMEBASE=1/1000\nSTART={start}\nEND={end}\n"
                     f"title={_ffmeta_escape(c['title'])}\n")
    return "".join(lines)


def youtube_list(chapters: list[dict]) -> list[str]:
    """"m:ss  Title" lines (YouTube description chapter format)."""
    out = []
    for c in chapters:
        t = int(c["start_s"])
        out.append(f"{t // 60}:{t % 60:02d}  {c['title']}")
    return out
