from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.generate_episode as generator

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES_PER_MODEL = 3
BACKOFF_SECONDS = (2, 5, 10)


def _status_code(exc: Exception) -> int | None:
    value = getattr(exc, "status_code", None)
    if value is None:
        value = getattr(exc, "code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_retryable(exc: Exception) -> bool:
    code = _status_code(exc)
    if code in RETRYABLE_STATUS:
        return True
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "429",
            "500",
            "502",
            "503",
            "504",
            "unavailable",
            "temporarily",
            "rate limit",
            "resource exhausted",
        )
    )


def _normalize_model(name: str) -> str:
    return name[7:] if name.startswith("models/") else name


def _available_models(client: genai.Client, requested: str) -> list[str]:
    names: list[str] = []
    requested = _normalize_model(requested.strip())
    if requested:
        names.append(requested)

    try:
        for item in client.models.list():
            name = _normalize_model(getattr(item, "name", "") or "")
            if not name or name in names:
                continue
            methods = (
                getattr(item, "supported_actions", None)
                or getattr(item, "supported_generation_methods", None)
                or []
            )
            if methods and not any("generate" in str(x).lower() for x in methods):
                continue
            lowered = name.lower()
            if any(
                x in lowered
                for x in ("embedding", "imagen", "veo", "tts", "audio", "live")
            ):
                continue
            names.append(name)
    except Exception:
        pass

    preferred = [name for name in generator.PREFERRED_MODELS if name in names]
    flash = [
        name
        for name in names
        if "flash" in name.lower() and "preview" not in name.lower()
        and name not in preferred
    ]
    rest = [
        name for name in names
        if name not in preferred and name not in flash
    ]
    return preferred + flash + rest


def _call_with_fallback(
    client: genai.Client,
    requested_model: str,
    call: Callable[[str], Any],
) -> Any:
    models = _available_models(client, requested_model)
    if not models:
        raise RuntimeError(
            "Gemini API에서 generateContent를 지원하는 모델을 찾지 못했습니다."
        )

    failures: list[str] = []
    for model in models:
        for attempt in range(MAX_RETRIES_PER_MODEL):
            try:
                return call(model)
            except Exception as exc:
                if not _is_retryable(exc):
                    raise
                failures.append(f"{model}: {exc}")
                if attempt + 1 < MAX_RETRIES_PER_MODEL:
                    time.sleep(BACKOFF_SECONDS[attempt])

    raise RuntimeError(
        "사용 가능한 Gemini 모델을 모두 시도했지만 일시적인 API 오류로 생성하지 못했습니다.\n"
        + "\n".join(failures[-12:])
    )


def resilient_json_call(client: genai.Client, model: str, prompt: str) -> Any:
    def call(candidate: str) -> Any:
        response = client.models.generate_content(
            model=candidate,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=generator.SYSTEM,
                response_mime_type="application/json",
                temperature=0.9,
                max_output_tokens=16384,
            ),
        )
        text = (response.text or "").strip()
        if not text:
            raise RuntimeError("모델이 빈 응답을 반환했습니다.")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"JSON 응답 파싱 실패: {text[:500]}") from exc

    return _call_with_fallback(client, model, call)


def resilient_text_call(client: genai.Client, model: str, prompt: str) -> str:
    def call(candidate: str) -> str:
        response = client.models.generate_content(
            model=candidate,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=generator.SYSTEM,
                temperature=0.95,
                max_output_tokens=16384,
            ),
        )
        text = (response.text or "").strip()
        if not text:
            raise RuntimeError("모델이 빈 본문을 반환했습니다.")
        return text

    return _call_with_fallback(client, model, call)


def main() -> int:
    generator.json_call = resilient_json_call
    generator.text_call = resilient_text_call
    return generator.main()


if __name__ == "__main__":
    raise SystemExit(main())
