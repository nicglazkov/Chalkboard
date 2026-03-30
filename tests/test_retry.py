# tests/test_retry.py
import asyncio
import pytest
from pipeline.retry import api_call_with_retry, TimeoutExhausted


def test_api_call_with_retry_returns_value():
    result = asyncio.run(
        api_call_with_retry(lambda: 42, timeout=5.0, label="test")
    )
    assert result == 42


def test_api_call_with_retry_retries_on_exception_and_succeeds():
    call_count = [0]

    def flaky():
        call_count[0] += 1
        if call_count[0] < 3:
            raise ConnectionError("network error")
        return "ok"

    result = asyncio.run(
        api_call_with_retry(flaky, timeout=5.0, max_attempts=3, label="test")
    )
    assert result == "ok"
    assert call_count[0] == 3


def test_api_call_with_retry_raises_timeout_exhausted_after_max_attempts():
    call_count = [0]

    def always_fails():
        call_count[0] += 1
        raise ValueError("bad")

    with pytest.raises(TimeoutExhausted) as exc_info:
        asyncio.run(
            api_call_with_retry(always_fails, timeout=5.0, max_attempts=3, label="myagent")
        )
    assert call_count[0] == 3
    assert "myagent" in str(exc_info.value)
    assert "3 attempts" in str(exc_info.value)


def test_api_call_with_retry_timeout_fires_and_exhausts():
    import time
    call_count = [0]

    def slow():
        call_count[0] += 1
        time.sleep(10)

    with pytest.raises(TimeoutExhausted):
        asyncio.run(
            api_call_with_retry(slow, timeout=0.05, max_attempts=2, label="test")
        )
    assert call_count[0] == 2


def test_api_call_with_retry_prints_notification(capsys):
    call_count = [0]

    def failing():
        call_count[0] += 1
        raise RuntimeError("oops")

    with pytest.raises(TimeoutExhausted):
        asyncio.run(
            api_call_with_retry(failing, timeout=5.0, max_attempts=2, label="mylabel")
        )

    captured = capsys.readouterr()
    assert "[mylabel]" in captured.out
    assert "attempt 2/2" in captured.out
