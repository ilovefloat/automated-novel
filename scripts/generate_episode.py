from __future__ import annotations

import argparse
import copy
import json
import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state"
EPISODES_DIR = ROOT / "docs" / "episodes"
STATE_PATH = STATE_DIR / "story_state.json"
MIN_CHARS = 700
MAX_ATTEMPTS = 3
KST = timezone(timedelta(hours=9), name="KST")
PREFERRED_MODELS = (
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
)
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
BACKOFF_SECONDS = (2, 5, 10)

SYSTEM = (ROOT / "prompts" / "system.md").read_text(encoding="utf-8")


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_state() -> dict[str, Any]:
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise RuntimeError("story_state.json은 객체여야 합니다.")
    return state


def save_state(state: dict[str, Any]) -> None:
    atomic_write(STATE_PATH, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def _status_code(exc: Exception) -> int | None:
    value = getattr(exc, "status_code", None)
    if value is None:
        value = getattr(exc, "code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _retryable(exc: Exception) -> bool:
    if _status_code(exc) in RETRYABLE_STATUS:
        return True
    text = str(exc).lower()
    return any(token in text for token in ("429", "500", "502", "503", "504", "unavailable", "temporarily", "rate limit", "resource exhausted"))


def _normalize_model(name: str) -> str:
    return name[7:] if name.startswith("models/") else name


def available_models(client: genai.Client, requested: str) -> list[str]:
    names: list[str] = []
    requested = _normalize_model(requested.strip())
    if requested:
        names.append(requested)
    try:
        for item in client.models.list():
            name = _normalize_model(getattr(item, "name", "") or "")
            if not name or name in names:
                continue
            methods = getattr(item, "supported_actions", None) or getattr(item, "supported_generation_methods", None) or []
            if methods and not any("generate" in str(x).lower() for x in methods):
                continue
            if any(x in name.lower() for x in ("embedding", "imagen", "veo", "tts", "audio", "live")):
                continue
            names.append(name)
    except Exception:
        pass
    preferred = [x for x in PREFERRED_MODELS if x in names]
    flash = [x for x in names if "flash" in x.lower() and "preview" not in x.lower() and x not in preferred]
    return preferred + flash + [x for x in names if x not in preferred and x not in flash]


def _with_model_fallback(client: genai.Client, requested: str, call: Callable[[str], Any]) -> Any:
    models = available_models(client, requested)
    if not models:
        raise RuntimeError("Gemini API에서 generateContent를 지원하는 모델을 찾지 못했습니다.")
    failures: list[str] = []
    for model in models:
        for attempt, delay in enumerate(BACKOFF_SECONDS, start=1):
            try:
                return call(model)
            except Exception as exc:
                if not _retryable(exc):
                    raise
                failures.append(f"{model}: {exc}")
                if attempt < len(BACKOFF_SECONDS):
                    time.sleep(delay)
    raise RuntimeError("Gemini 모델 fallback/retry가 모두 실패했습니다.\n" + "\n".join(failures[-12:]))


def json_call(client: genai.Client, model: str, prompt: str) -> Any:
    def call(candidate: str) -> Any:
        response = client.models.generate_content(
            model=candidate,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                response_mime_type="application/json",
                temperature=0.9,
                max_output_tokens=16384,
            ),
        )
        text = (response.text or "").strip()
        if not text:
            raise RuntimeError("모델이 빈 JSON 응답을 반환했습니다.")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"JSON 응답 파싱 실패: {text[:500]}") from exc

    return _with_model_fallback(client, model, call)


def text_call(client: genai.Client, model: str, prompt: str) -> str:
    def call(candidate: str) -> str:
        response = client.models.generate_content(
            model=candidate,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                temperature=0.95,
                max_output_tokens=16384,
            ),
        )
        text = (response.text or "").strip()
        if not text:
            raise RuntimeError("모델이 빈 본문을 반환했습니다.")
        return text

    return _with_model_fallback(client, model, call)


def choose_model(client: genai.Client) -> str:
    requested = os.environ.get("GEMINI_MODEL", "").strip()
    if requested:
        return _normalize_model(requested)
    models = available_models(client, "")
    return models[0] if models else PREFERRED_MODELS[0]


def recent_context(state: dict[str, Any]) -> str:
    history = state.get("history", [])
    return json.dumps(history[-8:] if isinstance(history, list) else [], ensure_ascii=False, indent=2)


def architect(client: genai.Client, model: str, state: dict[str, Any]) -> list[dict[str, Any]]:
    prompt = f"""다음은 지금까지 확정된 이야기 상태다.
{json.dumps(state, ensure_ascii=False, indent=2)}

최근 연재 기록:
{recent_context(state)}

다음 화를 위한 서로 다른 방향 5개를 제안하라. 실제 장면과 변화가 있는 서사적 방향이어야 한다.
각 후보에 다음 필드를 포함하라:
world_change, character_change, inner_author_action, fictional_event, meta_consequence,
future_questions, continuity_risks, repetition_risk, self_reference_potential,
premature_revelation_risk, long_term_potential.
이미 확정된 사실은 바꾸지 말라.
JSON 배열만 반환하라."""
    value = json_call(client, model, prompt)
    if not isinstance(value, list) or len(value) < 3:
        raise RuntimeError("서사 후보가 충분하지 않습니다.")
    return [x for x in value if isinstance(x, dict)][:5]


def judge(client: genai.Client, model: str, state: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = f"""현재 이야기 상태:
{json.dumps(state, ensure_ascii=False, indent=2)}

후보:
{json.dumps(candidates, ensure_ascii=False, indent=2)}

장편 지속성, 실제 변화, 원고와 세계의 동일성, 연속성, 메타 반복 위험, 다음 화의 여지를 검토하여 하나를 선택하라.
JSON으로 {{"selected_index": 정수, "reason": 문자열, "risks": [문자열]}}만 반환하라."""
    value = json_call(client, model, prompt)
    if not isinstance(value, dict):
        raise RuntimeError("후보 판정 응답이 잘못되었습니다.")
    index = int(value.get("selected_index", 0))
    if not 0 <= index < len(candidates):
        raise RuntimeError("잘못된 후보 인덱스입니다.")
    return value


def blueprint(client: genai.Client, model: str, state: dict[str, Any], direction: dict[str, Any], judgment: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""다음 화의 설계도를 만들어라.

상태:
{json.dumps(state, ensure_ascii=False, indent=2)}

선택된 방향:
{json.dumps(direction, ensure_ascii=False, indent=2)}

판정:
{json.dumps(judgment, ensure_ascii=False, indent=2)}

JSON 객체로 다음 필드를 정확히 포함하라.
episode_purpose, authorial_situation, story_events, character_changes, new_canon,
intentional_ambiguities, meta_movement, ending_state, must_not_happen.
meta_movement는 {{"reveal": ..., "question_raised": ..., "question_answered": ...}} 형태로 작성하라.
new_canon은 독자가 실제로 확인할 수 있는 객관적 사실만 넣어라. 인물의 추측을 사실로 확정하지 마라."""
    value = json_call(client, model, prompt)
    if not isinstance(value, dict):
        raise RuntimeError("설계도 응답이 잘못되었습니다.")
    required = ("episode_purpose", "authorial_situation", "story_events", "character_changes", "new_canon", "intentional_ambiguities", "meta_movement", "ending_state", "must_not_happen")
    missing = [key for key in required if key not in value]
    if missing:
        raise RuntimeError("설계도 필드 누락: " + ", ".join(missing))
    return value


def write_episode(client: genai.Client, model: str, state: dict[str, Any], direction: dict[str, Any], plan: dict[str, Any], failure: str = "") -> str:
    prompt = f"""다음 설계도대로 한국어 장편소설의 다음 화 본문을 작성하라.

현재 상태:
{json.dumps(state, ensure_ascii=False, indent=2)}

선택 방향:
{json.dumps(direction, ensure_ascii=False, indent=2)}

화 설계도:
{json.dumps(plan, ensure_ascii=False, indent=2)}

직전 검증 실패:
{failure or '없음'}

중요:
- 독자가 읽는 이 본문 자체가 이야기 내부에서 누군가 작성하고 있는 원고다.
- 별도의 '내부 소설'을 만들어 핵심 사건을 그 안에 숨기지 마라.
- 바깥의 해설자에게 진실을 설명하지 마라.
- '이것은 소설이다'라는 선언을 반복하지 마라.
- 구체적인 행동, 감각, 대화가 있어야 한다.
- 확정된 canon을 지키되 문학적으로 자연스럽게 쓴다.
- 제목이나 번호를 본문에 쓰지 않는다.
- 최소 {MIN_CHARS}자 이상 쓴다.
본문만 반환하라."""
    return text_call(client, model, prompt)


def _json_list(value: Any, error: str) -> list[str]:
    if not isinstance(value, list):
        return [error]
    return [str(x) for x in value]


def validate_meta(client: genai.Client, model: str, state: dict[str, Any], plan: dict[str, Any], body: str) -> list[str]:
    prompt = f"""다음 소설 본문이 구조적 메타픽션의 절대 규칙을 지키는지 검증하라.
절대 규칙: 독자가 읽는 텍스트와 이야기 내부 인물이 작성하는 원고는 동일하다.

본문:
{body}
설계도:
{json.dumps(plan, ensure_ascii=False, indent=2)}
기존 상태:
{json.dumps(state, ensure_ascii=False, indent=2)}

다음 위반만 찾아라: 별개의 중첩 소설, 외부 서술자/편집자의 진실 설명,
불필요한 별도 텍스트 층, 미확정 메타 진실의 단정, 기계적인 메타 장치 반복.
위반이 없으면 []만 반환하라."""
    return _json_list(json_call(client, model, prompt), "메타 검증 응답 형식 오류")


def validate_continuity(client: genai.Client, model: str, state: dict[str, Any], body: str) -> list[str]:
    prompt = f"""다음 본문을 기존 canon과 연속성 관점에서 검증하라.
기존 상태:
{json.dumps(state, ensure_ascii=False, indent=2)}
본문:
{body}

이름, 관계, 시간, 장소, 이미 일어난 사건, 죽음/부상, 객관적 사실을 임의로 바꾸었는지 검사하라.
인물의 믿음이나 가설을 객관적 사실로 승격시키는지도 검사하라.
문제가 없으면 []만 반환하라."""
    return _json_list(json_call(client, model, prompt), "연속성 검증 응답 형식 오류")


def validate_quality(client: genai.Client, model: str, state: dict[str, Any], body: str) -> list[str]:
    prompt = f"""소설 본문을 장편 연재의 품질 관점에서 검증하라.
기존 상태:
{json.dumps(state, ensure_ascii=False, indent=2)}
본문:
{body}

다음 문제가 있는 경우만 반환하라: 실제 사건/변화 부족, 철학적 설명만 존재,
이전 화 구조의 복제, 반복적인 메타 선언, 행동 인과 부족, 다음 상태 부재,
문학적으로 읽기 어려운 부자연스러움. 없으면 []만 반환하라."""
    return _json_list(json_call(client, model, prompt), "품질 검증 응답 형식 오류")


def extract_delta(client: genai.Client, model: str, state: dict[str, Any], body: str, plan: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""이 화를 읽고 story state에 적용할 최소한의 delta를 추출하라.
기존 상태:
{json.dumps(state, ensure_ascii=False, indent=2)}
설계도:
{json.dumps(plan, ensure_ascii=False, indent=2)}
본문:
{body}

JSON 객체:
{{
  "summary": 문자열,
  "ending_state": 문자열,
  "facts_added": [문자열],
  "characters_added_or_changed": [객체],
  "author_state": {{"identity": 문자열 또는 null, "knows_the_story_is_fiction": true/false/null, "knows_the_reader_exists": true/false/null}},
  "narrative_layer": 문자열 또는 null,
  "beliefs_added": [문자열],
  "hypotheses_added": [문자열],
  "open_questions_added": [문자열],
  "threads_added": [문자열],
  "threads_resolved": [문자열],
  "meta_facts_added": [문자열],
  "meta_questions_added": [문자열],
  "meta_anomalies_added": [문자열],
  "meta_revelation": 문자열 또는 null,
  "narrative_fingerprint": {{"meta_device": 문자열, "meta_revelation": 문자열, "author_action": 문자열, "fiction_reality_relation": 문자열, "narrative_layer": 문자열}}
}}
본문에 실제로 드러난 것만 기록하고 추론으로 새 사실을 만들지 마라.
특히 author_state는 본문에서 작가의 정체나 인식 수준이 명시되거나 명백하게 확정된 경우에만 갱신하라. 알 수 없으면 기존 값을 유지할 수 있도록 null을 사용하라.
narrative_layer도 본문에서 서술 층위가 명확하게 드러난 경우에만 기록하라."""
    value = json_call(client, model, prompt)
    if not isinstance(value, dict):
        raise RuntimeError("상태 delta 형식 오류")
    return value


def commit_delta(state: dict[str, Any], delta: dict[str, Any], episode: int) -> dict[str, Any]:
    new = copy.deepcopy(state)
    canon = new.setdefault("canon", {})
    for key in ("facts", "beliefs", "hypotheses", "unresolved_questions"):
        canon.setdefault(key, [])
    for source, target in (("facts_added", "facts"), ("beliefs_added", "beliefs"), ("hypotheses_added", "hypotheses"), ("open_questions_added", "unresolved_questions")):
        for item in delta.get(source, []):
            if item not in canon[target]:
                canon[target].append(item)

    chars = new.setdefault("characters", [])
    for item in delta.get("characters_added_or_changed", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        existing = next((x for x in chars if isinstance(x, dict) and x.get("name") == name), None)
        if existing is None:
            chars.append(item)
        else:
            existing.update(item)

    meta = new.setdefault("meta_state", {})
    author = meta.setdefault("author", {})
    author_delta = delta.get("author_state")
    if isinstance(author_delta, dict):
        for key in ("identity", "knows_the_story_is_fiction", "knows_the_reader_exists"):
            value = author_delta.get(key)
            if value is not None:
                author[key] = value

    narrative_layer = delta.get("narrative_layer")
    if isinstance(narrative_layer, str) and narrative_layer.strip():
        meta.setdefault("narrative_layer", {})["current_layer"] = narrative_layer.strip()

    for key in ("meta_facts", "meta_questions", "meta_anomalies"):
        meta.setdefault(key, [])
    for source, target in (("meta_facts_added", "meta_facts"), ("meta_questions_added", "meta_questions"), ("meta_anomalies_added", "meta_anomalies")):
        for item in delta.get(source, []):
            if item not in meta[target]:
                meta[target].append(item)

    threads = new.setdefault("open_threads", [])
    for item in delta.get("threads_resolved", []):
        threads[:] = [x for x in threads if x != item]
    for item in delta.get("threads_added", []):
        if item not in threads:
            threads.append(item)

    fingerprint = dict(delta.get("narrative_fingerprint") or {})
    fingerprint["episode"] = episode
    recent = new.setdefault("recent_fingerprints", [])
    recent.append(fingerprint)
    del recent[:-20]

    history = new.setdefault("history", [])
    history.append({
        "episode": episode,
        "summary": str(delta.get("summary", "")).strip()[:1000],
        "ending_state": str(delta.get("ending_state", "")).strip()[:1000],
        "meta_revelation": delta.get("meta_revelation"),
    })
    del history[:-20]
    new["next_episode"] = episode + 1
    new["last_generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return new


def fingerprint_repetition(state: dict[str, Any], delta: dict[str, Any]) -> list[str]:
    current = delta.get("narrative_fingerprint") or {}
    warnings: list[str] = []
    for old in state.get("recent_fingerprints", [])[-8:]:
        same = sum(bool(current.get(k)) and current.get(k) == old.get(k) for k in ("meta_device", "meta_revelation", "author_action", "fiction_reality_relation", "narrative_layer"))
        if same >= 4:
            warnings.append(f"최근 {old.get('episode')}화와 메타 장치가 과도하게 유사함")
    return warnings


def make_markdown(episode: int, body: str) -> str:
    today = datetime.now(KST).date().isoformat()
    title = f"{episode}화"
    return f"---\nepisode: {episode}\ntitle: \"{title}\"\ndate: \"{today}\"\n---\n\n# {title}\n\n{body.strip()}\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview-dir", type=Path)
    parser.add_argument("--result-json", type=Path)
    parser.add_argument("--skip-if-generated-today", action="store_true")
    args = parser.parse_args()

    state = load_state()
    episode = int(state.get("next_episode", 1))
    target = EPISODES_DIR / f"{episode:03d}.md"
    if target.exists():
        raise RuntimeError(f"다음 회차 파일이 이미 존재합니다: {target}")
    if args.skip_if_generated_today and state.get("last_generated_at"):
        last = datetime.fromisoformat(str(state["last_generated_at"]))
        if last.astimezone(KST).date() == datetime.now(KST).date():
            print("오늘 이미 회차가 생성되었습니다. 건너뜁니다.")
            return 0

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다.")
    client = genai.Client(api_key=api_key)
    model = choose_model(client)

    last_failure = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        candidates = architect(client, model, state)
        judgment = judge(client, model, state, candidates)
        direction = candidates[int(judgment["selected_index"])]
        plan = blueprint(client, model, state, direction, judgment)
        body = write_episode(client, model, state, direction, plan, last_failure)
        if len(body) < MIN_CHARS:
            last_failure = f"본문이 너무 짧습니다: {len(body)}자"
            continue
        errors = validate_meta(client, model, state, plan, body)
        errors += validate_continuity(client, model, state, body)
        errors += validate_quality(client, model, state, body)
        delta = extract_delta(client, model, state, body, plan)
        errors += fingerprint_repetition(state, delta)
        if errors:
            last_failure = "\n".join(f"- {x}" for x in errors)
            continue

        new_state = commit_delta(state, delta, episode)
        markdown = make_markdown(episode, body)
        if args.preview_dir:
            args.preview_dir.mkdir(parents=True, exist_ok=True)
            atomic_write(args.preview_dir / f"{episode:03d}.md", markdown)
            atomic_write(args.preview_dir / "story_state.json", json.dumps(new_state, ensure_ascii=False, indent=2) + "\n")
        else:
            atomic_write(target, markdown)
            try:
                save_state(new_state)
            except Exception:
                target.unlink(missing_ok=True)
                raise
        if args.result_json:
            atomic_write(args.result_json, json.dumps({"episode": episode, "status": "generated", "attempt": attempt}, ensure_ascii=False) + "\n")
        print(f"{episode}화 생성 완료 ({attempt}번째 시도, {model})")
        return 0

    raise RuntimeError(f"{MAX_ATTEMPTS}회 생성/검증 시도 모두 실패했습니다.\n{last_failure}")
