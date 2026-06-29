import os
import json
import re
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.core.errors import AppError

try:
    from google.genai.errors import ClientError
except Exception:  # pragma: no cover - google-genai may be absent in tests.
    ClientError = None


GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_MAX_ATTEMPTS = 3
DEFAULT_RATE_LIMIT_WAIT_SECONDS = 60
_last_validation_error: Exception | None = None


def get_gemini_api_key() -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if api_key:
        return api_key

    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)
    return os.environ.get("GEMINI_API_KEY", "").strip()


def call_gemini(
    prompt: str,
    system_instruction: str | None = None,
    response_mime_type: str | None = "application/json",
) -> str:
    api_key = get_gemini_api_key()
    if not api_key:
        raise AppError("LLM_API_KEY_MISSING", "GEMINI_API_KEY 환경변수가 설정되지 않았습니다")

    try:
        from google import genai
    except ImportError as exc:
        raise AppError("LLM_CLIENT_UNAVAILABLE", "google-genai 패키지가 설치되어 있지 않습니다") from exc

    client = genai.Client(api_key=api_key)
    config = {"system_instruction": system_instruction}
    if response_mime_type:
        config["response_mime_type"] = response_mime_type

    last_rate_limit_error: Exception | None = None
    for _ in range(GEMINI_MAX_ATTEMPTS):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=config,
            )
            return response.text
        except Exception as exc:
            if not _is_rate_limit_error(exc):
                raise
            wait_time = _rate_limit_wait_seconds(exc)
            print(f"Rate limited. Waiting {wait_time}s before retry...")
            time.sleep(wait_time)
            last_rate_limit_error = exc

    raise AppError("LLM_RATE_LIMITED", "Gemini API rate limit exceeded after 3 retries") from last_rate_limit_error


def call_gemini_with_validation(
    prompt: str,
    system_instruction: str,
    required_fields: dict,
    max_retries: int = 1,
) -> dict | list | None:
    global _last_validation_error
    _last_validation_error = None
    current_prompt = prompt
    for attempt in range(max_retries + 1):
        try:
            raw = call_gemini(current_prompt, system_instruction)
        except Exception as exc:
            _last_validation_error = exc
            return None
        parsed = try_parse_json(raw)
        if parsed is not None and validate_schema(parsed, required_fields):
            return parsed

        if attempt < max_retries:
            current_prompt = (
                f"{prompt}\n\n"
                "중요: 반드시 위에서 요청한 JSON 형식만 반환하세요. "
                "설명 문장이나 마크다운 코드블록 없이 유효한 JSON만 반환하세요."
            )

    return None


def get_last_validation_error() -> Exception | None:
    return _last_validation_error


def try_parse_json(text: str) -> dict | list | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def validate_schema(data: Any, required_fields: dict) -> bool:
    if isinstance(data, list):
        return all(validate_schema(item, required_fields) for item in data)
    if not isinstance(data, dict):
        return False
    for field, expected_type in required_fields.items():
        if field not in data:
            return False
        if expected_type and not isinstance(data[field], expected_type):
            return False
    return True


def _is_rate_limit_error(exc: Exception) -> bool:
    if ClientError is not None and isinstance(exc, ClientError):
        return getattr(exc, "status_code", None) == 429
    return getattr(exc, "status_code", None) == 429


def _rate_limit_wait_seconds(exc: Exception) -> int:
    wait_time = DEFAULT_RATE_LIMIT_WAIT_SECONDS
    try:
        text = str(exc)
        match = re.search(r"(\d+)\.?\d*s", text)
        if "retry" in text.lower() and match:
            wait_time = int(match.group(1)) + 5
    except Exception:
        pass
    return wait_time
