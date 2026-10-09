# pipeline/retry.py
import asyncio

try:
    import anthropic as _anthropic
    # Request errors that will fail the same way on every attempt.
    _FATAL = (
        _anthropic.BadRequestError, _anthropic.AuthenticationError,
        _anthropic.PermissionDeniedError, _anthropic.NotFoundError,
    )
except ImportError:  # pragma: no cover
    _FATAL = ()


def _is_busy(e: Exception) -> bool:
    text = str(e)
    return ("529" in text or "overloaded" in text.lower() or "429" in text
            or "rate limit" in text.lower())


def _is_fatal(e: Exception) -> bool:
    if isinstance(e, _FATAL):
        return True
    if "(not retried)" in str(e):
        return True
    # Out-of-credit 429s (OpenAI "insufficient_quota") never clear on retry.
    return "insufficient_quota" in str(e) or "credit balance is too low" in str(e)

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class TimeoutExhausted(Exception):
    """Raised when api_call_with_retry exhausts all attempts."""


# ---------------------------------------------------------------------------
# Timeout constants (seconds) — tune these as needed
# ---------------------------------------------------------------------------

# Claude calls think before answering, so budgets are generous; the SDK retries
# 429/5xx on its own inside each attempt.
TIMEOUT_SCRIPT_AGENT   = 300.0   # script_agent (may use web search tool)
TIMEOUT_RESEARCH_AGENT = 300.0   # research_agent (web search, may take multiple queries)
TIMEOUT_FACT_VALIDATOR = 180.0   # fact_validator
TIMEOUT_MANIM_AGENT    = 900.0   # manim_agent (base; llm.budget_timeout scales it with max_tokens)
TIMEOUT_CODE_VALIDATOR = 240.0   # code_validator
TIMEOUT_LAYOUT_CHECKER = 180.0   # layout_checker (headless dry-run — scales with animation count)
TIMEOUT_VISUAL_QA      = 240.0   # visual_qa (several base64 frames)
TIMEOUT_TTS_SEGMENT    =  60.0   # OpenAI + ElevenLabs per-segment (eleven_v3 is slower)
TIMEOUT_TTS_KOKORO     = 120.0   # Kokoro full call (includes model load)


# ---------------------------------------------------------------------------
# Retry wrapper
# ---------------------------------------------------------------------------

async def api_call_with_retry(fn, timeout, max_attempts=3, label="API call", passthrough=()):
    """
    Run sync callable `fn` in a thread with a timeout.
    Retry up to `max_attempts` times on timeouts and transient errors.
    Raises TimeoutExhausted when all attempts are exhausted, or immediately for
    request errors that cannot succeed on retry (bad request, auth, 404).
    Exceptions of a `passthrough` type are re-raised at once, unwrapped: the
    caller handles them (e.g. running out of output room, where repeating the
    identical call would fail the same way).
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout)
        except (asyncio.TimeoutError, Exception) as e:
            if passthrough and isinstance(e, passthrough):
                raise
            if _is_fatal(e):
                raise TimeoutExhausted(f"{label} failed: {e}") from e
            if attempt == max_attempts:
                raise TimeoutExhausted(
                    f"{label} failed after {max_attempts} attempts: {e}"
                )
            # Overload / rate limits outlast the SDK's own short retries: back off.
            busy = _is_busy(e)
            delay = (20.0 * attempt) if busy else 2.0
            print(
                f"  [{label}] failed ({type(e).__name__}{', service busy' if busy else ''}) — "
                f"retrying in {delay:.0f}s (attempt {attempt + 1}/{max_attempts})..."
            )
            await asyncio.sleep(delay)
