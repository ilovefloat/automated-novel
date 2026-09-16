from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from scripts.novelpia_content import parse_episode_markdown, normalize_text

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "state" / "novelpia_publish_state.json"


def load_state() -> dict:
    if STATE_PATH.exists():
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(value, dict): return value
    return {"published_episodes": [], "unknown_result_episodes": [], "publish_status": "never"}


def save_state(value: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode-path", required=True)
    parser.add_argument("--auth-state", type=Path, required=True)
    parser.add_argument("--refreshed-auth", type=Path, required=True)
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    parser.add_argument("--screenshot", type=Path, default=Path("preview/novelpia-editor.png"))
    parser.add_argument("--preview-only", action="store_true")
    parser.add_argument("--force-republish", action="store_true")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    content = parse_episode_markdown(args.episode_path)
    state = load_state()
    published = set(state.get("published_episodes", [])) | set(state.get("unknown_result_episodes", []))
    if content.episode in published and not args.force_republish:
        raise SystemExit("이미 게시되었거나 결과가 불명확한 회차입니다. 재게시하려면 --force-republish를 사용하세요.")

    editor_url = os.environ.get("NOVELPIA_EDITOR_URL", "").strip()
    if not editor_url:
        raise SystemExit("NOVELPIA_EDITOR_URL 변수가 없습니다. 새 작품의 작성 페이지를 등록하세요.")
    if urlparse(editor_url).hostname != "novelpia.com" or "/mynovel/all/write/" not in urlparse(editor_url).path:
        raise SystemExit("NOVELPIA_EDITOR_URL이 허용된 노벨피아 작성 URL이 아닙니다.")
    if not args.auth_state.is_file():
        raise SystemExit("NOVELPIA_AUTH_STATE_B64에서 auth state를 복원하지 못했습니다.")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not args.headed)
            context = browser.new_context(storage_state=str(args.auth_state))
            page = context.new_page()
            page.goto(editor_url, wait_until="domcontentloaded", timeout=45_000)
            page.locator("#content_subject").wait_for(state="visible", timeout=15_000)
            if not page.url.startswith("https://novelpia.com/mynovel/all/write/"):
                raise RuntimeError("노벨피아 로그인 세션이 만료되었거나 작성 페이지에 접근할 수 없습니다.")

            subject = page.locator("#content_subject")
            editor = page.locator('.note-editable[contenteditable="true"]')
            submit = page.locator("#submit_btn")
            if subject.count() != 1 or editor.count() != 1 or submit.count() != 1:
                raise RuntimeError("노벨피아 편집기 구조가 변경되었을 가능성이 있습니다.")
            subject.fill(content.title)
            editor.evaluate("(el, html) => { el.innerHTML = html; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); }", content.html_body)
            actual = normalize_text(editor.inner_text())
            expected = normalize_text(content.plain_text)
            if not actual or expected[:40] not in actual or expected[-40:] not in actual:
                raise RuntimeError("편집기에 입력된 본문 검증에 실패했습니다.")

            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.screenshot), full_page=True)
            if args.preview_only or os.environ.get("NOVELPIA_PUBLISH_ENABLED", "false").lower() != "true":
                print("preview: 작성 완료 버튼은 누르지 않았습니다.")
                browser.close()
                return 0

            submit.click()
            page.wait_for_timeout(1500)
            body = normalize_text(page.locator("body").inner_text())
            if re.search(r"(실패|오류가 발생)", body):
                raise RuntimeError("노벨피아가 게시 오류를 표시했습니다.")
            state["published_episodes"] = sorted(set(state.get("published_episodes", [])) | {content.episode})
            state["last_success_episode"] = content.episode
            state["last_success_title"] = content.title
            state["publish_status"] = "published"
            save_state(state)
            context.storage_state(path=str(args.refreshed_auth))
            browser.close()
            print(f"published: {content.episode}화")
            return 0
    except (PlaywrightTimeoutError, Exception) as exc:
        state["publish_status"] = "failed"
        state["last_error"] = str(exc)[:500]
        save_state(state)
        print(f"NOVELPIA_PUBLISH_FAILED: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
