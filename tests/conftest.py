"""Shared test fixtures."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from obs_react.db.schema import init_db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Create a temporary SQLite database for testing."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("obs_react.config.DB_PATH", db_path)
    conn = init_db(db_path)
    yield conn
    conn.close()


@pytest.fixture
def sample_announcement():
    """Sample announcement dict for testing."""
    return {
        "message_id": "12345",
        "ticker": "EQNR",
        "published_at": "2025-03-20T10:30:00+00:00",
        "category": "INSIDER TRADING",
        "title": "Test announcement title",
        "url": "https://newsweb.oslobors.no/message/12345",
        "issuer_name": "Equinor ASA",
    }


@pytest.fixture
def sample_price_bars():
    """Sample price bar dicts for testing."""
    return [
        {
            "ticker": "EQNR.OL",
            "timestamp": "2025-03-20T09:00:00+00:00",
            "interval": "1m",
            "open": 100.0,
            "high": 101.0,
            "low": 99.5,
            "close": 100.5,
            "volume": 10000,
        },
        {
            "ticker": "EQNR.OL",
            "timestamp": "2025-03-20T09:01:00+00:00",
            "interval": "1m",
            "open": 100.5,
            "high": 102.0,
            "low": 100.0,
            "close": 101.5,
            "volume": 15000,
        },
        {
            "ticker": "EQNR.OL",
            "timestamp": "2025-03-20T09:02:00+00:00",
            "interval": "1m",
            "open": 101.5,
            "high": 103.0,
            "low": 101.0,
            "close": 102.5,
            "volume": 20000,
        },
    ]
