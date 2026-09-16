from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import markdown
import nh3
import yaml

ROOT = Path(__file__).resolve().parents[1]
EPISODES_DIR = ROOT / "docs" / "episodes"
FRONT = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)

@dataclass(frozen=True)
class EpisodeContent:
    episode: int
    title: str
    date: str
    markdown_body: str
    html_body: str
    plain_text: str
    path: Path
    repository_path: str

class PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br": self.parts.append("\n")
    def handle_data(self, data: str) -> None: self.parts.append(data)

def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value).replace("\xa0", " ")).strip()

def html_to_plain_text(value: str) -> str:
    parser = PlainTextParser(); parser.feed(value)
    return normalize_text("".join(parser.parts))

def markdown_to_safe_html(body: str) -> str:
    body = re.sub(r"\n{3,}", "\n\n", body.replace("\r\n", "\n")).strip()
    rendered = markdown.markdown(body, extensions=["nl2br", "sane_lists"], output_format="html")
    cleaned = nh3.clean(rendered, tags={"p","br","strong","em","blockquote","hr","ul","ol","li"}, attributes={}, url_schemes=set())
    cleaned = re.sub(r"</p>\s*<p(?:\s[^>]*)?>", "<br><br>", cleaned, flags=re.I)
    cleaned = re.sub(r"</?p(?:\s[^>]*)?>", "", cleaned, flags=re.I)
    return re.sub(r">\s+<", "><", cleaned).strip()

def parse_episode_markdown(requested_path: str | Path) -> EpisodeContent:
    raw = Path(requested_path)
    if raw.is_absolute() or ".." in raw.parts or raw.suffix.lower() != ".md":
        raise ValueError("유효하지 않은 episode_path입니다.")
    root = ROOT.resolve(); path = (root / raw).resolve()
    allowed = (root / "docs" / "episodes").resolve()
    path.relative_to(allowed)
    text = path.read_text(encoding="utf-8")
    match = FRONT.match(text)
    if not match: raise ValueError("YAML front matter가 없습니다.")
    values: Any = yaml.safe_load(match.group(1))
    if not isinstance(values, dict): raise ValueError("front matter 형식 오류")
    episode = int(values["episode"])
    if episode != int(path.stem): raise ValueError("파일명과 episode 번호가 다릅니다.")
    title = str(values.get("title") or f"{episode}화").strip()
    public = text[match.end():].lstrip()
    if public.startswith("#"):
        public = public.split("\n", 1)[1] if "\n" in public else ""
    body = public.strip()
    if not body: raise ValueError("본문이 비어 있습니다.")
    safe = markdown_to_safe_html(body)
    return EpisodeContent(episode, title, str(values.get("date", "")), body, safe, html_to_plain_text(safe), path, path.relative_to(root).as_posix())
