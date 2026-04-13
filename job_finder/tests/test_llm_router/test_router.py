from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from errors import LLMParseError
from llm_router.router import LLMRouter


def test_extract_json_from_fenced_block_with_prose() -> None:
    raw = (
        "Here is the classification result:\n"
        "```json\n"
        '{"status":"REJECTED","confidence":0.91}\n'
        "```\n"
        "Done."
    )
    parsed = LLMRouter._extract_json(raw)
    assert parsed["status"] == "REJECTED"
    assert parsed["confidence"] == 0.91


def test_extract_json_from_embedded_object() -> None:
    raw = (
        "I reviewed the message and found an update. "
        '{"status":"RECEIVED","reason":"acknowledged"} '
        "Let me know if you want details."
    )
    parsed = LLMRouter._extract_json(raw)
    assert parsed["status"] == "RECEIVED"
    assert parsed["reason"] == "acknowledged"


def test_route_json_retries_with_stricter_prompt() -> None:
    router = LLMRouter()
    router.config["retry_on_parse_failure"] = 2
    router.route = MagicMock(side_effect=["not json", '{"status":"RECEIVED"}'])

    parsed = router.route_json(
        task_type="status_classification",
        system_prompt="system",
        user_prompt="original prompt",
    )

    assert parsed["status"] == "RECEIVED"
    assert router.route.call_count == 2
    second_prompt = router.route.call_args_list[1].args[2]
    assert "IMPORTANT: Return exactly one valid JSON object." in second_prompt
    assert "not json" in second_prompt


def test_route_json_raises_after_retry_exhaustion() -> None:
    router = LLMRouter()
    router.config["retry_on_parse_failure"] = 2
    router.route = MagicMock(side_effect=["still not json", "again not json", "and again not json"])

    with pytest.raises(LLMParseError):
        router.route_json(
            task_type="status_classification",
            system_prompt="system",
            user_prompt="prompt",
        )
