"""Shared helpers: .env loading, ElevenLabs REST calls, local state for created resource IDs."""
import json
import os
import pathlib
import sys

import requests

ROOT = pathlib.Path(__file__).parent
API = "https://api.elevenlabs.io/v1/convai"
STATE = ROOT / ".state.json"

for line in (ROOT / ".env").read_text().splitlines() if (ROOT / ".env").exists() else []:
    if "=" in line and not line.startswith("#"):
        k, v = line.removeprefix("export ").split("=", 1)
        if v := v.split(" #")[0].strip().strip("'\""):  # allow quotes and trailing comments, skip blanks
            os.environ.setdefault(k.strip(), v)


def env(key):
    return os.environ.get(key) or sys.exit(f"Missing {key} in .env")


def api(method, path, **kw):
    r = requests.request(method, API + path, headers={"xi-api-key": env("ELEVENLABS_API_KEY")}, timeout=60, **kw)
    if not r.ok:
        sys.exit(f"{method} {path} -> {r.status_code}: {r.text}")
    return r.json()


def rental_api(method, path):
    r = requests.request(method, env("PUBLIC_URL") + path, headers={"X-Api-Key": env("TOOL_TOKEN")}, timeout=30)
    r.raise_for_status()
    return r.json()


def state(**updates):
    s = json.loads(STATE.read_text()) if STATE.exists() else {}
    if updates:
        s.update(updates)
        STATE.write_text(json.dumps(s, indent=2))
    return s
