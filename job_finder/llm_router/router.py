"""
LLM Router — Routes requests to the appropriate LLM provider.

Phase 1-3: All tasks use a single primary model (Claude or GPT-4o).
Phase 5+: Per-task model routing via config overrides.

The router abstracts away provider differences (Anthropic, OpenAI, Ollama)
so agents simply call `router.route(task_type, system_prompt, user_prompt)`.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("job_finder.llm_router")


class LLMRouter:
    """Routes LLM requests to the appropriate provider based on task type."""

    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            config_path = str(
                Path(__file__).parent / "config.yaml"
            )

        raw = Path(config_path).read_text()
        # Resolve ${ENV_VAR} references in config
        resolved = self._resolve_env_vars(raw)
        self.config = yaml.safe_load(resolved)

        self._anthropic_client = None
        self._openai_client = None
        self._ollama_client = None

    @staticmethod
    def _resolve_env_vars(text: str) -> str:
        """Replace ${VAR} patterns with environment variable values."""
        def _replace(match):
            var_name = match.group(1)
            return os.getenv(var_name, match.group(0))
        return re.sub(r"\$\{(\w+)}", _replace, text)

    @property
    def anthropic_client(self):
        """Lazy-load Anthropic client."""
        if self._anthropic_client is None:
            from anthropic import Anthropic
            self._anthropic_client = Anthropic()
        return self._anthropic_client

    @property
    def openai_client(self):
        """Lazy-load OpenAI client."""
        if self._openai_client is None:
            from openai import OpenAI
            self._openai_client = OpenAI()
        return self._openai_client

    @property
    def ollama_client(self):
        """Lazy-load Ollama client."""
        if self._ollama_client is None:
            from ollama import Client as OllamaClient
            base_url = self.config.get("local", {}).get(
                "base_url", "http://localhost:11434"
            )
            self._ollama_client = OllamaClient(host=base_url)
        return self._ollama_client

    def _get_model_config(self, task_type: str) -> dict:
        """Get model config for a task — check overrides, fall back to default."""
        overrides = self.config.get("task_routing", {})
        if overrides and task_type in overrides:
            return overrides[task_type]
        return {
            "model": self.config.get("default_model", "claude-sonnet-4-20250514"),
            "temperature": self.config.get("default_temperature", 0.3),
            "max_tokens": self.config.get("default_max_tokens", 4096),
        }

    def route(
        self,
        task_type: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """
        Route a request to the appropriate LLM and return the response text.

        Args:
            task_type: Identifies which agent is calling (e.g. "profile_analysis").
            system_prompt: The system message.
            user_prompt: The user message.

        Returns:
            The LLM's response as a string (typically JSON).

        Raises:
            LLMParseError: If the response cannot be parsed after retries.
        """
        model_config = self._get_model_config(task_type)

        if task_type == "pii_injection":
            return self._call_ollama(system_prompt, user_prompt, model_config)

        model_name = model_config["model"]
        if model_name and "claude" in model_name.lower():
            try:
                return self._call_anthropic(system_prompt, user_prompt, model_config)
            except Exception as exc:
                import anthropic as _anthropic
                # If the primary Claude model is overloaded after all retries, fall back to
                # a lighter Claude model (haiku) which runs on a different Anthropic queue.
                is_overloaded = (
                    isinstance(exc, _anthropic.APIStatusError)
                    and exc.status_code in (429, 500, 502, 503, 504, 529)
                )
                if not is_overloaded:
                    raise
                fallback_model = os.getenv("ANTHROPIC_FALLBACK_MODEL", "claude-haiku-4-5-20251001")
                # Don't fall back to the same model that just failed.
                if fallback_model == model_name:
                    raise
                logger.warning(
                    "Anthropic model '%s' overloaded after all retries. "
                    "Falling back to lighter Claude model: %s",
                    model_name,
                    fallback_model,
                )
                fallback_config = {
                    "model": fallback_model,
                    "temperature": model_config.get("temperature", 0.3),
                    "max_tokens": model_config.get("max_tokens", 4096),
                }
                # Prefix system prompt with a hard JSON-only directive so the
                # lighter fallback model doesn't return markdown prose.
                json_prefix = (
                    "CRITICAL INSTRUCTION: You must respond with a single, valid JSON object only. "
                    "No markdown, no code fences, no explanatory prose — pure JSON starting with { "
                    "and ending with }. Do not include ```json or ``` wrappers.\n\n"
                )
                patched_system = json_prefix + system_prompt
                return self._call_anthropic(patched_system, user_prompt, fallback_config)
        else:
            return self._call_openai(system_prompt, user_prompt, model_config)

    def route_json(
        self,
        task_type: str,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        """
        Route a request and parse the response as JSON, with retries.

        Returns:
            Parsed JSON dict.

        Raises:
            LLMParseError after max retries.
        """
        from errors import LLMParseError

        max_retries = max(3, int(self.config.get("retry_on_parse_failure", 3)))
        retry_prompt = user_prompt

        # Prepend a hard JSON-only directive so models never wrap output in
        # code fences or explanatory prose on the very first attempt.
        json_directive = (
            "CRITICAL: You MUST respond with a single, valid JSON object ONLY. "
            "No markdown, no code fences (```), no explanatory text — just raw JSON "
            "starting with { and ending with }.\n\n"
        )
        patched_system = json_directive + system_prompt

        for attempt in range(max_retries):
            raw = self.route(task_type, patched_system, retry_prompt)
            try:
                return self._extract_json(raw)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(
                    f"JSON parse failed (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt == max_retries - 1:
                    raise LLMParseError(
                        f"Failed to parse JSON after {max_retries} attempts. "
                        f"Last response: {raw[:500]}",
                        raw_response=raw,
                    ) from e
                retry_prompt = self._build_retry_prompt_for_json(
                    original_user_prompt=user_prompt,
                    last_response=raw,
                )

        raise LLMParseError("Failed to parse JSON response.", raw_response=None)

    @staticmethod
    def _build_retry_prompt_for_json(original_user_prompt: str, last_response: str) -> str:
        """Build a stricter retry prompt after a malformed JSON response."""
        clipped = (last_response or "").strip()
        if len(clipped) > 1200:
            clipped = f"{clipped[:1200]}..."
        return (
            f"{original_user_prompt}\n\n"
            "IMPORTANT: Return exactly one valid JSON object.\n"
            "Rules:\n"
            "- Output JSON only (no markdown, no prose)\n"
            "- Use double quotes for all keys and string values\n"
            "- Do not include trailing commas\n\n"
            "Your previous response was not valid JSON:\n"
            f"{clipped}\n"
        )

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """Extract a JSON object from an LLM response."""
        normalized = (text or "").strip().lstrip("\ufeff")
        if not normalized:
            raise ValueError("Empty LLM response; expected JSON object.")

        candidates: list[str] = []
        for match in re.finditer(r"```(?:json)?\s*\n?(.*?)\n?\s*```", normalized, re.DOTALL | re.IGNORECASE):
            block = match.group(1).strip()
            if block:
                candidates.append(block)
        candidates.append(normalized)

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                parsed = LLMRouter._decode_first_json_object(candidate)

            if isinstance(parsed, dict):
                return parsed

        raise ValueError("No valid JSON object found in model response.")

    @staticmethod
    def _decode_first_json_object(text: str) -> dict[str, Any] | None:
        """Best-effort extraction of the first JSON object in free-form text."""
        decoder = json.JSONDecoder()
        for idx, char in enumerate(text):
            if char not in "{[":
                continue
            try:
                parsed, _ = decoder.raw_decode(text[idx:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    def _call_anthropic(
        self, system_prompt: str, user_prompt: str, config: dict
    ) -> str:
        """Call Claude via the Anthropic API."""
        import anthropic
        import time

        logger.info(f"Calling Anthropic: {config['model']}")
        
        max_attempts = 5
        base_delay = 2.0

        for attempt in range(max_attempts):
            try:
                response = self.anthropic_client.messages.create(
                    model=config["model"],
                    max_tokens=config.get("max_tokens", 4096),
                    temperature=config.get("temperature", 0.3),
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                return response.content[0].text
            except anthropic.APIStatusError as e:
                # 529 (OverloadedError), 429 (RateLimitError), 500 (InternalServerError) etc.
                if attempt == max_attempts - 1:
                    raise
                
                # Only retry on rate limits or server-side transient errors
                if e.status_code not in (429, 500, 502, 503, 504, 529):
                    raise
                    
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    f"Anthropic API error ({e.status_code}). "
                    f"Retrying in {delay}s... (Attempt {attempt + 1}/{max_attempts})"
                )
                time.sleep(delay)
                
        raise RuntimeError("Failed to call Anthropic API after max retries")

    def _call_openai(
        self, system_prompt: str, user_prompt: str, config: dict
    ) -> str:
        """Call GPT via the OpenAI API."""
        logger.info(f"Calling OpenAI: {config['model']}")
        response = self.openai_client.chat.completions.create(
            model=config["model"],
            temperature=config.get("temperature", 0.3),
            max_tokens=config.get("max_tokens", 4096),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content

    def _call_ollama(
        self, system_prompt: str, user_prompt: str, config: dict
    ) -> str:
        """Call a local model via Ollama."""
        local_config = self.config.get("local", {})
        model = local_config.get("model", "phi3")
        temperature = local_config.get("temperature", 0.1)

        logger.info(f"Calling Ollama (local): {model}")
        response = self.ollama_client.chat(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={"temperature": temperature},
        )
        return response["message"]["content"]
