from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state"
EPISODES_DIR = ROOT / "docs" / "episodes"
STATE_PATH = STATE_DIR / "story_state.json"
MODEL_CATALOG = STATE_DIR / "model_catalog.json"
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

SYSTEM = (ROOT / "prompts" / "system.md").read_text(encoding="utf-8")


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        raise RuntimeError("state/story_state.json이 없습니다.")
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise RuntimeError("story_state.json은 객체여야 합니다.")
    return state


def save_state(state: dict[str, Any]) -> None:
    atomic_write(STATE_PATH, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def json_call(client: genai.Client, model: str, prompt: str) -> Any:
    response = client.models.generate_content(
        model=model,
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
        raise RuntimeError("모델이 빈 응답을 반환했습니다.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"JSON 응답 파싱 실패: {text[:500]}") from exc


def text_call(client: genai.Client, model: str, prompt: str) -> str:
    response = client.models.generate_content(
        model=model,
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


def choose_model(client: genai.Client) -> str:
    requested = os.environ.get("GEMINI_MODEL", "").strip()
    if requested:
        return requested
    try:
        names = []
        for item in client.models.list():
            name = getattr(item, "name", "") or ""
            if name.startswith("models/"):
                name = name[7:]
            methods = getattr(item, "supported_actions", None) or getattr(item, "supported_generation_methods", None) or []
            if methods and not any("generate" in str(x).lower() for x in methods):
                continue
            lowered = name.lower()
            if any(x in lowered for x in ("embedding", "imagen", "veo", "tts", "audio", "live")):
                continue
            names.append(name)
        for preferred in PREFERRED_MODELS:
            if preferred in names:
                return preferred
        for name in names:
            if "flash" in name.lower() and "preview" not in name.lower():
                return name
        if names:
            return names[0]
    except Exception:
        pass
    return PREFERRED_MODELS[0]


def recent_context(state: dict[str, Any]) -> str:
    history = state.get("history", [])
    if not isinstance(history, list):
        history = []
    return json.dumps(history[-8:], ensure_ascii=False, indent=2)


def architect(client: genai.Client, model: str, state: dict[str, Any]) -> list[dict[str, Any]]:
    prompt = f"""다음은 지금까지 확정된 이야기 상태다.
{json.dumps(state, ensure_ascii=False, indent=2)}

최근 연재 기록:
{recent_context(state)}

다음 화를 위한 서로 다른 방향 5개를 제안하라. 단순히 배경이나 장르를 고르는 후보가 아니라 실제 장면과 변화가 있는 서사적 방향이어야 한다.
각 후보는 다음 필드를 가져라:
- world_change
- character_change
- inner_author_action
- fictional_event
- meta_consequence
- future_questions
- continuity_risks
- repetition_risk
- self_reference_potential
- premature_revelation_risk
- long_term_potential

초기 상태라면 세계와 인물을 자연스럽게 만들어도 된다. 이미 확정된 사실은 바꾸지 말라.
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

후보를 단순 점수 경쟁으로 취급하지 말고 다음을 검토하여 하나를 선택하라.
1. 장편으로 계속될 수 있는가
2. 현재 인물과 세계를 실제로 움직이는가
3. 원고와 이야기 세계의 동일성이라는 핵심 구조를 강화하는가
4. 기존 사실과 충돌하지 않는가
5. 메타 장치를 반복하거나 너무 일찍 정답으로 만들 위험이 작은가
6. 다음 화에 새로운 선택지를 남기는가

JSON으로 {"selected_index": 정수, "reason": 문자열, "risks": [문자열]}만 반환하라."""
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
- episode_purpose: 이번 화의 서사적 목적
- authorial_situation: 이야기 내부에서 원고가 어떤 상황에 놓이는가
- story_events: 실제로 일어나는 사건 목록
- character_changes: 인물의 상태/관계 변화
- new_canon: 이번 화에서 객관적으로 확정할 사실
- intentional_ambiguities: 일부러 열린 채 둘 문제
- meta_movement: {"reveal": ..., "question_raised": ..., "question_answered": ...}
- ending_state: 화 마지막의 실제 상태
- must_not_happen: 이번 화에서 하면 안 되는 것

특히 new_canon은 독자가 실제로 확인할 수 있는 사실만 넣어라. 인물의 추측을 사실로 확정하지 마라.
"""
    value = json_call(client, model, prompt)
    if not isinstance(value, dict):
        raise RuntimeError("설계도 응답이 잘못되었습니다.")
    required = ("episode_purpose", "authorial_situation", "story_events", "character_changes", "new_canon", "intentional_ambiguities", "meta_movement", "ending_state", "must_not_happen")
    for key in required:
        if key not in value:
            raise RuntimeError(f"설계도 필드 누락: {key}")
    return value


def write_episode(client: genai.Client, model: str, state: dict[str, Any], direction: dict[str, Any], plan: dict[str, Any], failure: str = "") -> str:
    prompt = f"""다음 설계도대로 한국어 장편소설의 다음 화 본문을 작성하라.

현재 상태:
{json.dumps(state, ensure_ascii=False, indent=2)}

선택 방향:
{json.dumps(direction, ensure_ascii=False, indent=2)}

화 설계도:
{json.dumps(plan, ensure_ascii=False, indent=2)}

직전 검증 실패가 있다면 반드시 반영하라:
{failure or '없음'}

중요:
- 독자가 읽는 이 본문 자체가 이야기 내부에서 누군가 작성하고 있는 원고다.
- 핵심 사건을 별도의 '내부 소설'에 넣고 그것을 인용하는 방식으로 회피하지 마라.
- 바깥의 해설자에게 진실을 알려주지 마라.
- '이것은 소설이다'라는 선언을 반복해서 메타성을 만들지 마라.
- 구체적인 행동과 감각, 대화가 있어야 한다.
- 설계도와 확정된 canon을 지키되 문학적으로 자연스럽게 쓴다.
- 제목이나 번호를 본문에 쓰지 않는다.
- 최소 {MIN_CHARS}자 이상 충분한 분량으로 쓴다.
본문만 반환하라."""
    return text_call(client, model, prompt)


def validate_meta(client: genai.Client, model: str, state: dict[str, Any], plan: dict[str, Any], body: str) -> list[str]:
    prompt = f"""다음 소설 본문이 구조적 메타픽션의 절대 규칙을 지키는지 검증하라.

절대 규칙: 독자가 읽는 텍스트와 이야기 내부 인물이 작성하는 원고는 동일하다.

본문:
{body}

설계도:
{json.dumps(plan, ensure_ascii=False, indent=2)}

기존 상태:
{json.dumps(state, ensure_ascii=False, indent=2)}

다음 위반만 찾아라.
- 별개의 중첩 소설을 만들어 실제 이야기를 그 안에 숨김
- 외부 서술자/편집자가 세계의 진실을 직접 설명함
- 작성 행위와 독자가 읽는 원고 사이에 불필요한 별도 텍스트 층을 만듦
- 확정되지 않은 메타 진실을 사실로 단정함
- 같은 메타 장치를 기계적으로 반복함

JSON 배열로 위반 사항을 반환하고, 없으면 []을 반환하라."""
    value = json_call(client, model, prompt)
    return [str(x) for x in value] if isinstance(value, list) else ["메타 검증 응답 형식 오류"]


def validate_continuity(client: genai.Client, model: str, state: dict[str, Any], body: str) -> list[str]:
    prompt = f"""다음 본문을 기존 canon과 연속성 관점에서 검증하라.

기존 상태:
{json.dumps(state, ensure_ascii=False, indent=2)}

본문:
{body}

이름, 관계, 시간, 장소, 이미 일어난 사건, 죽음/부상, 객관적 사실을 임의로 바꾸었는지 검사하라.
인물의 믿음이나 가설을 객관적 사실로 승격시키는지도 검사하라.
문제가 없으면 []만 반환하라."""
    value = json_call(client, model, prompt)
    return [str(x) for x in value] if isinstance(value, list) else ["연속성 검증 응답 형식 오류"]


def validate_quality(client: genai.Client, model: str, state: dict[str, Any], body: str) -> list[str]:
    prompt = f"""소설 본문을 장편 연재의 품질 관점에서 검증하라.

기존 상태:
{json.dumps(state, ensure_ascii=False, indent=2)}

본문:
{body}

문제가 있는 경우만 반환하라.
- 실제 사건/변화가 거의 없음
- 철학적 설명만 있고 장면이 없음
- 이전 화의 구조를 그대로 복제함
- 지나치게 반복적인 메타 선언
- 인물 행동의 인과가 없음
- 연재물로서 다음 상태가 남지 않음
- 문학적으로 읽기 어려울 정도의 부자연스러움
없으면 []을 반환하라."""
    value = json_call(client, model, prompt)
    return [str(x) for x in value] if isinstance(value, list) else ["품질 검증 응답 형식 오류"]


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
  "facts_added": [문자열],
  "characters_added_or_changed": [객체],
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

본문에 실제로 드러난 것만 기록하고 추론으로 새 사실을 만들지 마라."""
    value = json_call(client, model, prompt)
    if not isinstance(value, dict):
        raise RuntimeError("상태 delta 형식 오류")
    return value


def commit_delta(state: dict[str, Any], delta: dict[str, Any], episode: int, body: str) -> dict[str, Any]:
    new = json.loads(json.dumps(state, ensure_ascii=False))
    canon = new.setdefault("canon", {})
    for key in ("facts", "beliefs", "hypotheses", "unresolved_questions"):
        canon.setdefault(key, [])
    for source, target in (("facts_added", "facts"), ("beliefs_added", "beliefs"), ("hypotheses_added", "hypotheses"), ("open_questions_added", "unresolved_questions")):
        for item in delta.get(source, []):
            if item not in canon[target]:
                canon[target].append(item)
    chars = new.setdefault("characters", [])
    for item in delta.get("characters_added_or_changed", []):
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            if name:
                existing = next((x for x in chars if isinstance(x, dict) and x.get("name") == name), None)
                if existing is None:
                    chars.append(item)
                else:
                    existing.update(item)
    meta = new.setdefault("meta_state", {})
    meta.setdefault("meta_facts", [])
    meta.setdefault("meta_questions", [])
    meta.setdefault("meta_anomalies", [])
    for source, target in (("meta_facts_added", "meta_facts"), ("meta_questions_added", "meta_questions"), ("meta_anomalies_added", "meta_anomalies")):
        for item in delta.get(source, []):
            if item not in meta[target]:
                meta[target].append(item)
    new.setdefault("open_threads", [])
    for item in delta.get("threads_resolved", []):
        new["open_threads"] = [x for x in new["open_threads"] if x != item]
    for item in delta.get("threads_added", []):
        if item not in new["open_threads"]:
            new["open_threads"].append(item)
    fingerprint = dict(delta.get("narrative_fingerprint") or {})
    fingerprint["episode"] = episode
    recent = new.setdefault("recent_fingerprints", [])
    recent.append(fingerprint)
    del recent[:-20]
    new.setdefault("history", []).append({
        "episode": episode,
        "summary": str(delta.get("summary", ""))[:1000],
        "ending_state": str(delta.get("ending_state", ""))[:1000],
        "meta_revelation": delta.get("meta_revelation"),
    })
    del new["history"][:-20]
    new["next_episode"] = episode + 1
    new["last_generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return new


def fingerprint_repetition(state: dict[str, Any], delta: dict[str, Any]) -> list[str]:
    current = delta.get("narrative_fingerprint") or {}
    recent = state.get("recent_fingerprints", [])[-8:]
    warnings = []
    for old in recent:
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
    if args.skip_if_generated_today and state.get("last_generated_at"):
        try:
            last = datetime.fromisoformat(state["last_generated_at"])
            if last.astimezone(KST).date() == datetime.now(KST).date():
                result = {"mode": "skipped", "episode_path": ""}
                if args.result_json:
                    args.result_json.write_text(json.dumps(result), encoding="utf-8")
                return 0
        except ValueError:
            pass

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("GEMINI_API_KEY가 설정되지 않았습니다.")
    client = genai.Client(api_key=api_key)
    model = choose_model(client)
    episode = int(state.get("next_episode", 1))
    EPISODES_DIR.mkdir(parents=True, exist_ok=True)

    candidates = architect(client, model, state)
    judgment = judge(client, model, state, candidates)
    direction = candidates[int(judgment["selected_index"])]
    plan = blueprint(client, model, state, direction, judgment)

    body = ""
    failure = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        body = write_episode(client, model, state, direction, plan, failure)
        errors = []
        if len(re.sub(r"\s+", "", body)) < MIN_CHARS:
            errors.append(f"본문이 너무 짧음: 최소 {MIN_CHARS}자")
        errors.extend(validate_meta(client, model, state, plan, body))
        errors.extend(validate_continuity(client, model, state, body))
        errors.extend(validate_quality(client, model, state, body))
        if not errors:
            break
        failure = "\n".join(f"- {x}" for x in errors)
        if attempt == MAX_ATTEMPTS:
            raise SystemExit("검증을 통과하지 못해 발행하지 않습니다.\n" + failure)

    delta = extract_delta(client, model, state, body, plan)
    repeat = fingerprint_repetition(state, delta)
    if repeat:
        raise SystemExit("메타 장치 반복 검증 실패:\n" + "\n".join(repeat))

    new_state = commit_delta(state, delta, episode, body)
    path = EPISODES_DIR / f"{episode:03d}.md"
    if path.exists():
        raise SystemExit(f"이미 존재하는 에피소드 파일입니다: {path}")

    preview = args.preview_dir is not None
    if preview:
        out = args.preview_dir / path.name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(make_markdown(episode, body), encoding="utf-8")
        mode = "preview"
        episode_path = ""
    else:
        atomic_write(path, make_markdown(episode, body))
        save_state(new_state)
        mode = "publish"
        episode_path = path.relative_to(ROOT).as_posix()

    if args.result_json:
        args.result_json.parent.mkdir(parents=True, exist_ok=True)
        args.result_json.write_text(json.dumps({"mode": mode, "episode_path": episode_path, "model": model}, ensure_ascii=False), encoding="utf-8")
    print(f"{mode}: episode={episode}, model={model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
