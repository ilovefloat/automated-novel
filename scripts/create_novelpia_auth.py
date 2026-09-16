from __future__ import annotations

from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path("novelpia-auth.json")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://novelpia.com", wait_until="domcontentloaded")
    print("브라우저에서 노벨피아에 로그인한 뒤 이 터미널로 돌아와 Enter를 누르세요.")
    input()
    context.storage_state(path=str(OUT))
    browser.close()
print(f"저장 완료: {OUT.resolve()}")
