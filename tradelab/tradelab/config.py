import hashlib
from pathlib import Path

import yaml

from tradelab import journal

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def load(path=DEFAULT_PATH):
    with open(path, "rb") as f:
        return yaml.safe_load(f)


def sha256(path=DEFAULT_PATH):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ConfigChanged(Exception):
    pass


def lock(conn, path=DEFAULT_PATH):
    """First call writes the config hash to the journal; later calls must match it."""
    h = sha256(path)
    locked = journal.events(conn, "config_sha256")
    if not locked:
        journal.log_event(conn, "config_sha256", {"sha256": h, "path": str(path)})
        return h
    expected = locked[0]["payload"]["sha256"]
    if h != expected:
        journal.log_event(conn, "config_mismatch", {"expected": expected, "got": h})
        raise ConfigChanged(f"config.yaml changed after lock: {expected} != {h}")
    return h
