import os

TTS_BACKEND    = os.getenv("TTS_BACKEND", "kokoro")   # "kokoro" | "openai" | "elevenlabs"
MANIM_QUALITY  = os.getenv("MANIM_QUALITY", "medium") # "low" | "medium" | "high"
DEFAULT_EFFORT = os.getenv("DEFAULT_EFFORT", "medium")
DEFAULT_AUDIENCE = os.getenv("DEFAULT_AUDIENCE", "intermediate")
OUTPUT_DIR     = os.getenv("OUTPUT_DIR", "./output")
CHECKPOINT_DB  = os.getenv("CHECKPOINT_DB", "pipeline_state.db")
CLAUDE_MODEL   = "claude-sonnet-4-6"

