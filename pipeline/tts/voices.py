# pipeline/tts/voices.py
"""Narrators: named voices that pin (TTS backend, voice id, model id).

Pick one per run with --narrator / the API `narrator` field / NARRATOR in .env.
Leaving it unset keeps the legacy behaviour: TTS_BACKEND with that backend's
default voice.

The ElevenLabs voices came out of a blind listening test (April 2026) across
OpenAI, Fish, ElevenLabs and Gemini, then a v4 vs v3 audition (October 2026)
that kept Aria (Skye) and Milo (Bradley) on eleven_v4. Voice ids are pinned
because the ElevenLabs library can rename voices.
"""
from __future__ import annotations

import os
from typing import TypedDict


class NarratorSpec(TypedDict):
    backend: str    # "kokoro" | "elevenlabs" | "openai"
    voice: str      # backend-specific voice id
    model: str      # backend-specific model id ("" = backend default)
    label: str
    tagline: str


_ELEVEN_MODEL = os.getenv("ELEVENLABS_MODEL_ID", "eleven_v4")

NARRATORS: dict[str, NarratorSpec] = {
    # Free, local (GPU if available)
    "kokoro": {"backend": "kokoro", "voice": "af_heart", "model": "",
               "label": "Kokoro", "tagline": "Free local voice (Kokoro-82M)."},
    # ElevenLabs (needs ELEVENLABS_API_KEY)
    "aria":   {"backend": "elevenlabs", "voice": "1iNDh1muacMMMHXvS7Ym", "model": _ELEVEN_MODEL,
               "label": "Aria", "tagline": "Young, bright female with a little nerdy charm (Skye)."},
    "milo":   {"backend": "elevenlabs", "voice": "RexqLjNzkCjWogguKyff", "model": _ELEVEN_MODEL,
               "label": "Milo", "tagline": "Earnest male narrator (Bradley)."},
    # OpenAI (needs OPENAI_API_KEY)
    "alloy":  {"backend": "openai", "voice": "alloy", "model": "gpt-4o-mini-tts",
               "label": "Alloy", "tagline": "OpenAI gpt-4o-mini-tts."},
}

ALLOWED_NARRATORS = tuple(NARRATORS)


def resolve(narrator: str | None) -> NarratorSpec | None:
    """Spec for a narrator name; None means 'use TTS_BACKEND defaults'."""
    if not narrator:
        return None
    try:
        return NARRATORS[narrator]
    except KeyError:
        raise ValueError(f"Unknown narrator {narrator!r}. Choose: {', '.join(ALLOWED_NARRATORS)}")
