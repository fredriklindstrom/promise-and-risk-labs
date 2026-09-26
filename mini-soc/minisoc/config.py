"""Paths and settings. Everything lives in the project folder except the API key."""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
DB_PATH = STATE_DIR / "minisoc.sqlite"
STATE_JSON = STATE_DIR / "state.json"
LOG_PATH = STATE_DIR / "watcher.log"

SEVERITIES = ["info", "low", "medium", "high"]


def load():
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["key_file"] = str(pathlib.Path(cfg["key_file"]).expanduser())
    return cfg


def seed():
    p = ROOT / "baseline_seed.json"
    return json.loads(p.read_text()) if p.exists() else {}


def sev_at_least(sev, floor):
    return SEVERITIES.index(sev) >= SEVERITIES.index(floor)
