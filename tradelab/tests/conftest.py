import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tradelab import config, journal  # noqa: E402


@pytest.fixture
def cfg():
    return copy.deepcopy(config.load())


@pytest.fixture
def conn(tmp_path):
    c = journal.connect(tmp_path / "journal.sqlite")
    yield c
    c.close()


def random_bars(n, seed=0, start="2018-01-01"):
    """Test fixture only: random-walk OHLCV, never used as a stand-in for market data."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="D", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.04, n)))
    open_ = np.concatenate([[100.0], close[:-1]])
    width = np.abs(rng.normal(0, 0.02, n))
    return pd.DataFrame(
        {"open": open_, "high": np.maximum(open_, close) * (1 + width),
         "low": np.minimum(open_, close) * (1 - width), "close": close,
         "quote_volume": rng.uniform(1e8, 1e9, n)},
        index=idx,
    )


@pytest.fixture
def bars():
    return random_bars(1500)
