"""Tests for news scraper normalization (no network calls)."""

from obs_react.news.scraper import _normalize_message, _extract_messages_from_json, parse_announcement


class TestNormalizeMessage:
    def test_standard_fields(self):
        raw = {
            "messageId": "12345",
            "ticker": "EQNR",
            "publishedTime": "2025-03-20T10:30:00+01:00",
            "category": "INSIDER TRADING",
            "title": "Test announcement",
            "url": "https://newsweb.oslobors.no/message/12345",
            "issuerName": "Equinor ASA",
        }
        result = _normalize_message(raw)
        assert result is not None
        assert result["message_id"] == "12345"
        assert result["ticker"] == "EQNR"
        assert result["issuer_name"] == "Equinor ASA"

    def test_alternative_field_names(self):
        raw = {
            "disclosureId": "67890",
            "issuerSign": "mowi",
            "publishedAt": "2025-03-20T10:30:00Z",
            "categoryName": "ANNUAL REPORT",
            "headline": "Mowi annual report",
        }
        result = _normalize_message(raw)
        assert result is not None
        assert result["message_id"] == "67890"
        assert result["ticker"] == "MOWI"
        assert result["category"] == "ANNUAL REPORT"
        assert result["title"] == "Mowi annual report"

    def test_missing_id_returns_none(self):
        raw = {"ticker": "EQNR", "title": "No ID"}
        result = _normalize_message(raw)
        assert result is None

    def test_missing_ticker_returns_none(self):
        raw = {"messageId": "12345", "title": "No ticker"}
        result = _normalize_message(raw)
        assert result is None

    def test_url_generated_from_message_id(self):
        raw = {"messageId": "12345", "ticker": "EQNR", "title": "Test"}
        result = _normalize_message(raw)
        assert "12345" in result["url"]

    def test_nested_issuer_ticker(self):
        raw = {
            "id": "99999",
            "issuer": {"sign": "DNB", "name": "DNB ASA"},
            "title": "DNB announcement",
        }
        result = _normalize_message(raw)
        assert result is not None
        assert result["ticker"] == "DNB"

    def test_z_suffix_timezone(self):
        raw = {
            "messageId": "111",
            "ticker": "EQNR",
            "publishedTime": "2025-03-20T10:30:00Z",
            "title": "Test",
        }
        result = _normalize_message(raw)
        assert "+00:00" in result["published_at"]


class TestExtractMessagesFromJson:
    def test_list_input(self):
        data = [
            {"messageId": "1", "ticker": "EQNR", "title": "A"},
            {"messageId": "2", "ticker": "MOWI", "title": "B"},
        ]
        messages = _extract_messages_from_json(data)
        assert len(messages) == 2

    def test_dict_with_messages_key(self):
        data = {"messages": [{"messageId": "1", "ticker": "EQNR", "title": "A"}]}
        messages = _extract_messages_from_json(data)
        assert len(messages) == 1

    def test_dict_with_data_key(self):
        data = {"data": [{"messageId": "1", "ticker": "EQNR", "title": "A"}]}
        messages = _extract_messages_from_json(data)
        assert len(messages) == 1

    def test_empty_list(self):
        assert _extract_messages_from_json([]) == []

    def test_empty_dict(self):
        assert _extract_messages_from_json({}) == []


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
