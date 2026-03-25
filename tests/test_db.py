"""Tests for database layer: schema, operations."""

import sqlite3

import pytest

from obs_react.db.schema import init_db, get_connection
from obs_react.db.operations import (
    insert_announcement,
    get_announcement_by_message_id,
    get_known_message_ids,
    get_announcements,
    insert_price_bars,
    get_price_bars,
    get_price_bar_range,
    upsert_stock_meta,
    get_stock_meta,
    get_all_stock_meta,
    insert_event_result,
    get_event_results,
    log_fetch_start,
    log_fetch_end,
    get_last_fetch,
    get_db_stats,
)


class TestSchema:
    def test_init_db_creates_tables(self, tmp_db):
        tables = tmp_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = {t["name"] for t in tables}
        assert "announcements" in table_names
        assert "price_bars" in table_names
        assert "stock_meta" in table_names
        assert "event_results" in table_names
        assert "fetch_log" in table_names

    def test_init_db_idempotent(self, tmp_path, monkeypatch):
        db_path = tmp_path / "test2.db"
        monkeypatch.setattr("obs_react.config.DB_PATH", db_path)
        conn1 = init_db(db_path)
        conn1.close()
        conn2 = init_db(db_path)
        conn2.close()


class TestAnnouncements:
    def test_insert_and_retrieve(self, tmp_db, sample_announcement):
        row_id = insert_announcement(**sample_announcement, conn=tmp_db)
        assert row_id is not None

        ann = get_announcement_by_message_id("12345", conn=tmp_db)
        assert ann is not None
        assert ann.ticker == "EQNR"
        assert ann.category == "INSIDER TRADING"

    def test_duplicate_insert_returns_none(self, tmp_db, sample_announcement):
        insert_announcement(**sample_announcement, conn=tmp_db)
        result = insert_announcement(**sample_announcement, conn=tmp_db)
        assert result is None

    def test_get_known_message_ids(self, tmp_db, sample_announcement):
        insert_announcement(**sample_announcement, conn=tmp_db)
        ids = get_known_message_ids(conn=tmp_db)
        assert "12345" in ids

    def test_get_announcements_filter_by_ticker(self, tmp_db, sample_announcement):
        insert_announcement(**sample_announcement, conn=tmp_db)
        results = get_announcements(ticker="EQNR", conn=tmp_db)
        assert len(results) == 1
        assert results[0].ticker == "EQNR"

        results = get_announcements(ticker="NONEXIST", conn=tmp_db)
        assert len(results) == 0

    def test_get_announcements_filter_by_category(self, tmp_db, sample_announcement):
        insert_announcement(**sample_announcement, conn=tmp_db)
        results = get_announcements(category="INSIDER TRADING", conn=tmp_db)
        assert len(results) == 1

    def test_get_nonexistent_announcement(self, tmp_db):
        ann = get_announcement_by_message_id("nonexistent", conn=tmp_db)
        assert ann is None


class TestPriceBars:
    def test_insert_and_retrieve(self, tmp_db, sample_price_bars):
        inserted = insert_price_bars(sample_price_bars, conn=tmp_db)
        assert inserted == 3

        bars = get_price_bars("EQNR.OL", conn=tmp_db)
        assert len(bars) == 3
        assert bars[0].close == 100.5

    def test_insert_empty_list(self, tmp_db):
        assert insert_price_bars([], conn=tmp_db) == 0

    def test_duplicate_bars_ignored(self, tmp_db, sample_price_bars):
        insert_price_bars(sample_price_bars, conn=tmp_db)
        inserted = insert_price_bars(sample_price_bars, conn=tmp_db)
        assert inserted == 0

    def test_filter_by_interval(self, tmp_db, sample_price_bars):
        insert_price_bars(sample_price_bars, conn=tmp_db)
        bars = get_price_bars("EQNR.OL", interval="1m", conn=tmp_db)
        assert len(bars) == 3
        bars = get_price_bars("EQNR.OL", interval="5m", conn=tmp_db)
        assert len(bars) == 0

    def test_get_price_bar_range(self, tmp_db, sample_price_bars):
        insert_price_bars(sample_price_bars, conn=tmp_db)
        mn, mx = get_price_bar_range("EQNR.OL", "1m", conn=tmp_db)
        assert mn == "2025-03-20T09:00:00+00:00"
        assert mx == "2025-03-20T09:02:00+00:00"

    def test_get_price_bar_range_empty(self, tmp_db):
        mn, mx = get_price_bar_range("NONE.OL", "1m", conn=tmp_db)
        assert mn is None
        assert mx is None


class TestStockMeta:
    def test_upsert_and_retrieve(self, tmp_db):
        upsert_stock_meta(
            ticker="EQNR.OL",
            company_name="Equinor ASA",
            market_cap=5e10,
            avg_daily_volume=1e6,
            sector="Energy",
            industry="Oil",
            market_cap_bucket="large",
            volume_bucket="high",
            conn=tmp_db,
        )
        meta = get_stock_meta("EQNR.OL", conn=tmp_db)
        assert meta is not None
        assert meta.company_name == "Equinor ASA"
        assert meta.market_cap_bucket == "large"

    def test_upsert_updates_existing(self, tmp_db):
        upsert_stock_meta(ticker="EQNR.OL", company_name="Equinor", conn=tmp_db)
        upsert_stock_meta(ticker="EQNR.OL", company_name="Equinor ASA", market_cap=5e10, conn=tmp_db)
        meta = get_stock_meta("EQNR.OL", conn=tmp_db)
        assert meta.company_name == "Equinor ASA"
        assert meta.market_cap == 5e10

    def test_get_all_stock_meta(self, tmp_db):
        upsert_stock_meta(ticker="EQNR.OL", company_name="Equinor", conn=tmp_db)
        upsert_stock_meta(ticker="DNB.OL", company_name="DNB", conn=tmp_db)
        all_meta = get_all_stock_meta(conn=tmp_db)
        assert len(all_meta) == 2


class TestEventResults:
    def test_insert_and_retrieve(self, tmp_db, sample_announcement):
        insert_announcement(**sample_announcement, conn=tmp_db)
        ann = get_announcement_by_message_id("12345", conn=tmp_db)

        insert_event_result(
            announcement_id=ann.id,
            ticker="EQNR.OL",
            window_name="[-5m,+5m]",
            abnormal_return=0.015,
            cumulative_ar=0.02,
            reaction_time_seconds=120,
            data_quality="full",
            conn=tmp_db,
        )
        results = get_event_results(announcement_id=ann.id, conn=tmp_db)
        assert len(results) == 1
        assert results[0].abnormal_return == 0.015


class TestFetchLog:
    def test_log_start_and_end(self, tmp_db):
        log_id = log_fetch_start("newsweb", conn=tmp_db)
        assert log_id > 0
        log_fetch_end(log_id, records_fetched=42, conn=tmp_db)

        entry = get_last_fetch("newsweb", conn=tmp_db)
        assert entry is not None
        assert entry["records_fetched"] == 42

    def test_log_with_error(self, tmp_db):
        log_id = log_fetch_start("yfinance_price", "EQNR.OL", conn=tmp_db)
        log_fetch_end(log_id, error_message="timeout", conn=tmp_db)

        entry = get_last_fetch("yfinance_price", "EQNR.OL", conn=tmp_db)
        assert entry["error_message"] == "timeout"


class TestDbStats:
    def test_stats_empty_db(self, tmp_db):
        stats = get_db_stats(conn=tmp_db)
        assert stats["announcements_count"] == 0
        assert stats["price_bars_count"] == 0
        assert stats["distinct_tickers"] == 0

    def test_stats_with_data(self, tmp_db, sample_announcement, sample_price_bars):
        insert_announcement(**sample_announcement, conn=tmp_db)
        insert_price_bars(sample_price_bars, conn=tmp_db)

        stats = get_db_stats(conn=tmp_db)
        assert stats["announcements_count"] == 1
        assert stats["price_bars_count"] == 3
        assert stats["distinct_tickers"] == 1
