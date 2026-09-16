from __future__ import annotations

import argparse
import base64
import os
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("auth_file", type=Path)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()
    token = os.environ.get("GH_TOKEN", "").strip()
    if not token:
        raise SystemExit("GH_TOKEN이 없습니다.")
    data = base64.b64encode(args.auth_file.read_bytes()).decode("ascii")
    subprocess.run(["gh", "secret", "set", "NOVELPIA_AUTH_STATE_B64", "--repo", args.repo, "--body", data], check=True, env={**os.environ, "GH_TOKEN": token})
    print("NOVELPIA_AUTH_STATE_B64 updated")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
