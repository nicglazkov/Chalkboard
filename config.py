from dotenv import load_dotenv
load_dotenv()
import os

TTS_BACKEND    = os.getenv("TTS_BACKEND", "kokoro")   # "kokoro" | "openai" | "elevenlabs"
MANIM_QUALITY  = os.getenv("MANIM_QUALITY", "medium") # "low" | "medium" | "high" | "4k"
DEFAULT_EFFORT = os.getenv("DEFAULT_EFFORT", "medium")
DEFAULT_AUDIENCE = os.getenv("DEFAULT_AUDIENCE", "intermediate")
DEFAULT_TONE    = os.getenv("DEFAULT_TONE", "casual")
DEFAULT_THEME   = os.getenv("DEFAULT_THEME", "chalkboard")
OUTPUT_DIR     = os.getenv("OUTPUT_DIR", "./output")
CHECKPOINT_DB  = os.getenv("CHECKPOINT_DB", "pipeline_state.db")
SERVER_HOST    = os.getenv("SERVER_HOST", "127.0.0.1")
SERVER_PORT    = int(os.getenv("SERVER_PORT", "8000"))

# Where scenes are rendered:
#   "local"  - manim + LaTeX installed on this machine (fastest; see README)
#   "docker" - the chalkboard-render image (no local TeX needed)
#   "auto"   - local if `manim` and `latex` are on PATH, else docker
RENDER_BACKEND = os.getenv("RENDER_BACKEND", "auto")

# ── Claude models ────────────────────────────────────────────────────────────
# One model for everything by default. Override globally with CLAUDE_MODEL, or
# per agent with CLAUDE_MODEL_<AGENT> (e.g. CLAUDE_MODEL_CODE_VALIDATOR=claude-sonnet-5-5).
# Agents: research, script, fact, manim, code_validator, visual_qa, quiz.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")

# Effort (thinking depth / spend) per agent. Scene code generation is where
# quality is won or lost, so it gets the most; validators run lean.
_AGENT_EFFORT = {
    "research":       "medium",
    "script":         "high",
    "fact":           "medium",
    "manim":          "high",
    "code_validator": "medium",
    "visual_qa":      "high",
    "quiz":           "low",
}


def agent_model(agent: str) -> str:
    return os.getenv(f"CLAUDE_MODEL_{agent.upper()}", CLAUDE_MODEL)


def agent_effort(agent: str) -> str:
    return os.getenv(f"CLAUDE_EFFORT_{agent.upper()}", _AGENT_EFFORT.get(agent, "medium"))
