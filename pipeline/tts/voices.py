# pipeline/tts/voices.py
"""Narrators: named voices that pin (TTS backend, voice id, model id).

Pick one per run with --narrator / the API `narrator` field / NARRATOR in .env.
Leaving it unset keeps the legacy behaviour: TTS_BACKEND with that backend's
default voice.

The ElevenLabs voices came out of a blind listening test (April 2026) across
OpenAI, Fish, ElevenLabs and Gemini; voice ids are pinned because the
ElevenLabs library can rename voices.
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


_ELEVEN_MODEL = os.getenv("ELEVENLABS_MODEL_ID", "eleven_v3")

NARRATORS: dict[str, NarratorSpec] = {
    # Free, local (GPU if available)
    "kokoro": {"backend": "kokoro", "voice": "af_heart", "model": "",
               "label": "Kokoro", "tagline": "Free local voice (Kokoro-82M)."},
    # ElevenLabs (needs ELEVENLABS_API_KEY)
    "aria":   {"backend": "elevenlabs", "voice": "1iNDh1muacMMMHXvS7Ym", "model": _ELEVEN_MODEL,
               "label": "Aria", "tagline": "Young, bright female with a little nerdy charm (Skye)."},
    "milo":   {"backend": "elevenlabs", "voice": "RexqLjNzkCjWogguKyff", "model": _ELEVEN_MODEL,
               "label": "Milo", "tagline": "Earnest male narrator (Bradley)."},
    "reid":   {"backend": "elevenlabs", "voice": "yr43K8H5LoTp6S1QFSGg", "model": _ELEVEN_MODEL,
               "label": "Reid", "tagline": "Natural conversational male (Matt)."},
    "grace":  {"backend": "elevenlabs", "voice": "WQhVGGVQ8EhNpBYHFE8c", "model": _ELEVEN_MODEL,
               "label": "Grace", "tagline": "Warm, clear female (Layla)."},
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
