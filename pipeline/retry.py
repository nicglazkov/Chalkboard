# pipeline/retry.py
import asyncio

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class TimeoutExhausted(Exception):
    """Raised when api_call_with_retry exhausts all attempts."""


# ---------------------------------------------------------------------------
# Timeout constants (seconds) — tune these as needed
# ---------------------------------------------------------------------------

TIMEOUT_SCRIPT_AGENT   = 120.0   # script_agent (may use web search tool)
TIMEOUT_FACT_VALIDATOR =  60.0   # fact_validator
TIMEOUT_MANIM_AGENT    = 180.0   # manim_agent (max_tokens=16384)
TIMEOUT_CODE_VALIDATOR =  60.0   # code_validator
TIMEOUT_VISUAL_QA      =  90.0   # visual_qa (5 base64 images)
TIMEOUT_TTS_SEGMENT    =  30.0   # OpenAI + ElevenLabs per-segment
TIMEOUT_TTS_KOKORO     = 120.0   # Kokoro full call (includes model load)


# ---------------------------------------------------------------------------
# Retry wrapper
# ---------------------------------------------------------------------------

async def api_call_with_retry(fn, timeout, max_attempts=3, label="API call"):
    """
    Run sync callable `fn` in a thread with a timeout.
    Retry up to `max_attempts` times on any exception or timeout.
    Raises TimeoutExhausted when all attempts are exhausted.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout)
        except (asyncio.TimeoutError, Exception) as e:
            if attempt == max_attempts:
                raise TimeoutExhausted(
                    f"{label} failed after {max_attempts} attempts: {e}"
                )
            print(
                f"  [{label}] failed ({type(e).__name__}) — "
                f"retrying (attempt {attempt + 1}/{max_attempts})..."
            )
