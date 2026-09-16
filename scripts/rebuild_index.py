from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EPISODES = ROOT / "docs" / "episodes"
OUT = ROOT / "docs" / "episodes.json"
FRONT = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.DOTALL)

items = []
for path in sorted(EPISODES.glob("*.md")):
    text = path.read_text(encoding="utf-8")
    match = FRONT.match(text)
    if not match:
        continue
    values = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip().strip('"')
    items.append({"episode": int(values["episode"]), "title": values.get("title", f"{values['episode']}화"), "date": values.get("date", ""), "path": f"episodes/{path.name}"})
items.sort(key=lambda x: x["episode"])
OUT.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"indexed {len(items)} episodes")
