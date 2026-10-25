from pathlib import Path

import pandas as pd

from tradelab import journal

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data" / "holdout"


class HoldoutViolation(Exception):
    pass


def load(allow=False, *, conn, path=DEFAULT_DIR):
    """The only reader of holdout data. allow=True works exactly once per journal."""
    if not allow:
        journal.log_event(conn, "holdout_violation", {"reason": "allow=False", "path": str(path)})
        raise HoldoutViolation("holdout read without allow=True")
    if journal.events(conn, "holdout_read"):
        journal.log_event(conn, "holdout_violation", {"reason": "second read", "path": str(path)})
        raise HoldoutViolation("holdout already read once")
    journal.log_event(conn, "holdout_read", {"path": str(path)})
    path = Path(path)
    files = [path] if path.is_file() else sorted(path.glob("*.parquet"))
    return {f.stem: pd.read_parquet(f) for f in files}
