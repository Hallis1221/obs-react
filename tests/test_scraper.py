"""Tests for news scraper normalization (no network calls)."""

from obs_react.news.scraper import _normalize_api_message, parse_announcement


class TestNormalizeApiMessage:
    def test_standard_fields(self):
        raw = {
            "messageId": 12345,
            "issuerSign": "EQNR",
            "publishedTime": "2025-03-20T10:30:00+01:00",
            "category": [{"category_en": "INSIDER TRADING"}],
            "title": "Test announcement",
            "issuerName": "Equinor ASA",
        }
        result = _normalize_api_message(raw)
        assert result is not None
        assert result["message_id"] == "12345"
        assert result["ticker"] == "EQNR"
        assert result["issuer_name"] == "Equinor ASA"
        assert result["category"] == "INSIDER TRADING"

    def test_lowercase_ticker_uppercased(self):
        raw = {
            "messageId": 1,
            "issuerSign": "mowi",
            "title": "Test",
        }
        result = _normalize_api_message(raw)
        assert result is not None
        assert result["ticker"] == "MOWI"

    def test_missing_id_returns_none(self):
        raw = {"issuerSign": "EQNR", "title": "No ID"}
        result = _normalize_api_message(raw)
        assert result is None

    def test_missing_ticker_returns_none(self):
        raw = {"messageId": 12345, "title": "No ticker"}
        result = _normalize_api_message(raw)
        assert result is None

    def test_url_generated_from_message_id(self):
        raw = {"messageId": 12345, "issuerSign": "EQNR", "title": "Test"}
        result = _normalize_api_message(raw)
        assert "12345" in result["url"]

    def test_z_suffix_timezone(self):
        raw = {
            "messageId": 111,
            "issuerSign": "EQNR",
            "publishedTime": "2025-03-20T10:30:00Z",
            "title": "Test",
        }
        result = _normalize_api_message(raw)
        assert "+00:00" in result["published_at"]

    def test_category_list_of_dicts(self):
        raw = {
            "messageId": 1,
            "issuerSign": "DNB",
            "category": [
                {"category_en": "INSIDE INFORMATION", "category_no": "INNSIDEINFORMASJON"},
            ],
            "title": "Test",
        }
        result = _normalize_api_message(raw)
        assert result["category"] == "INSIDE INFORMATION"

    def test_category_multiple(self):
        raw = {
            "messageId": 1,
            "issuerSign": "DNB",
            "category": [
                {"category_en": "INSIDE INFORMATION"},
                {"category_en": "ANNUAL REPORT"},
            ],
            "title": "Test",
        }
        result = _normalize_api_message(raw)
        assert "INSIDE INFORMATION" in result["category"]
        assert "ANNUAL REPORT" in result["category"]

    def test_category_string_fallback(self):
        raw = {
            "messageId": 1,
            "issuerSign": "DNB",
            "category": "SOME CATEGORY",
            "title": "Test",
        }
        result = _normalize_api_message(raw)
        assert result["category"] == "SOME CATEGORY"

    def test_empty_category(self):
        raw = {
            "messageId": 1,
            "issuerSign": "DNB",
            "category": [],
            "title": "Test",
        }
        result = _normalize_api_message(raw)
        assert result["category"] == "UNKNOWN"

    def test_no_published_time(self):
        raw = {
            "messageId": 1,
            "issuerSign": "DNB",
            "title": "Test",
        }
        result = _normalize_api_message(raw)
        assert result["published_at"] == ""


class TestParseAnnouncement:
    def test_normalizes_fields(self):
        raw = {
            "message_id": "12345",
            "ticker": " eqnr ",
            "published_at": "2025-03-20T10:30:00+00:00",
            "category": "INSIDER TRADING",
            "title": "Test",
            "url": "https://example.com",
            "issuer_name": "Equinor",
        }
        result = parse_announcement(raw)
        assert result["ticker"] == "EQNR"
        assert result["message_id"] == "12345"

    def test_defaults_for_missing(self):
        raw = {"message_id": "1", "ticker": "X"}
        result = parse_announcement(raw)
        assert result["category"] == "UNKNOWN"
        assert result["title"] == ""
