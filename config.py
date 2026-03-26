import os

TTS_BACKEND    = os.getenv("TTS_BACKEND", "kokoro")   # "kokoro" | "openai" | "elevenlabs"
MANIM_QUALITY  = os.getenv("MANIM_QUALITY", "medium") # "low" | "medium" | "high"
DEFAULT_EFFORT = os.getenv("DEFAULT_EFFORT", "medium")
OUTPUT_DIR     = os.getenv("OUTPUT_DIR", "./output")
CHECKPOINT_DB  = os.getenv("CHECKPOINT_DB", "pipeline_state.db")
CLAUDE_MODEL   = "claude-sonnet-4-6"

QUALITY_FLAGS = {"low": "-ql", "medium": "-qm", "high": "-qh"}
QUALITY_SUBDIRS = {"low": "480p15", "medium": "720p30", "high": "1080p60"}
