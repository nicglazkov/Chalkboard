from dotenv import load_dotenv
load_dotenv()
import os

TTS_BACKEND    = os.getenv("TTS_BACKEND", "kokoro")   # "kokoro" | "openai" | "elevenlabs"
# Named voice (pipeline/tts/voices.py), e.g. "aria"; overrides TTS_BACKEND when set.
NARRATOR       = os.getenv("NARRATOR", "")
MANIM_QUALITY  = os.getenv("MANIM_QUALITY", "medium") # "low" | "medium" | "high" | "4k"
DEFAULT_EFFORT = os.getenv("DEFAULT_EFFORT", "medium")
DEFAULT_AUDIENCE = os.getenv("DEFAULT_AUDIENCE", "intermediate")
DEFAULT_TONE    = os.getenv("DEFAULT_TONE", "casual")
DEFAULT_THEME   = os.getenv("DEFAULT_THEME", "chalkboard")
# Presentation pacing preset (pipeline/pacing.py): relaxed | normal | brisk.
# Per-run --pace / API `pace` override it; SCENE_HOLD_S and PACE_SPEECH_SPEED
# override single values of the preset.
PACE           = (os.getenv("PACE", "") or "relaxed").strip().lower()
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
# One model for everything by default (CLAUDE_MODEL), except where a cheaper
# model measured as good (visual_qa: Sonnet 5.5 found the same errors as Opus
# on the 2026-10-09 comparison, at about half the price). Override per agent
# with CLAUDE_MODEL_<AGENT> (e.g. CLAUDE_MODEL_VISUAL_QA=claude-opus-5-5).
# Agents: research, script, fact, manim, code_validator, visual_qa, quiz.
# The scene agent has two sub-roles with their own effort: manim_plan (the
# visual plan of a scene written in parts) and manim_fix (every revision:
# review, layout and visual-QA fixes). A sub-role's settings fall back to the
# manim ones, so CLAUDE_EFFORT_MANIM=high sets all three.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")

_AGENT_MODEL = {
    "visual_qa": "claude-sonnet-5-5",
}

# Effort (thinking depth / spend) per agent. Thinking is billed as output and
# was 75-80% of the scene agent's output at effort high (2026-10-09: ~76k of
# ~96k output tokens for one 8-segment lecture scene); see CHANGELOG 0.7.0 for
# the measured sweep behind these defaults.
_AGENT_EFFORT = {
    "research":       "medium",
    "script":         "high",
    "fact":           "medium",
    "manim":          "low",
    "manim_plan":     "medium",
    "manim_fix":      "high",
    "code_validator": "medium",
    "visual_qa":      "high",
    "quiz":           "low",
}

# Sub-roles that inherit their parent agent's env overrides.
_PARENT = {"manim_plan": "manim", "manim_fix": "manim"}


def _env(kind: str, agent: str) -> str | None:
    v = os.getenv(f"{kind}_{agent.upper()}")
    if not v and agent in _PARENT:
        v = os.getenv(f"{kind}_{_PARENT[agent].upper()}")
    return v or None


def agent_model(agent: str) -> str:
    return (_env("CLAUDE_MODEL", agent)
            or _AGENT_MODEL.get(agent) or _AGENT_MODEL.get(_PARENT.get(agent, ""))
            or CLAUDE_MODEL)


def agent_effort(agent: str) -> str:
    return _env("CLAUDE_EFFORT", agent) or _AGENT_EFFORT.get(agent, "medium")
