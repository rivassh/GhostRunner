"""Unit tests for transformer.py - Layer 2 HAR parser."""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from transformer import parse_har_file, generate_flow_prompt, _is_media_request, _parse_entry


# ─── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def sample_har(tmp_path):
    """Create a sample HAR file with mixed media and API requests."""
    har_data = {
        "log": {
            "version": "1.2",
            "creator": {"name": "Test", "version": "1.0"},
            "entries": [
                {
                    "request": {
                        "method": "GET",
                        "url": "https://api.example.com/data",
                        "headers": [
                            {"name": "Authorization", "value": "Bearer token123"},
                            {"name": "Content-Type", "value": "application/json"},
                        ],
                        "queryString": [{"name": "page", "value": "1"}],
                        "cookies": [],
                        "resourceType": "document",
                    },
                    "response": {"status": 200, "content": {"size": 1024}},
                    "pageType": "document",
                },
                {
                    "request": {
                        "method": "POST",
                        "url": "https://api.example.com/submit",
                        "headers": [{"name": "Authorization", "value": "Bearer token123"}],
                        "queryString": [],
                        "cookies": [],
                        "postData": {"mimeType": "application/json", "text": '{"key":"val"}'},
                        "resourceType": "xhr",
                    },
                    "response": {"status": 201, "content": {"size": 512}},
                },
                {
                    "request": {
                        "method": "GET",
                        "url": "https://cdn.example.com/style.css",
                        "headers": [],
                        "queryString": [],
                        "cookies": [],
                        "resourceType": "stylesheet",
                    },
                    "response": {"status": 200},
                },
                {
                    "request": {
                        "method": "GET",
                        "url": "https://cdn.example.com/image.png",
                        "headers": [],
                        "queryString": [],
                        "cookies": [],
                        "resourceType": "image",
                    },
                    "response": {"status": 200},
                },
                {
                    "request": {
                        "method": "GET",
                        "url": "https://api.example.com/data?page=2",
                        "headers": [{"name": "Authorization", "value": "Bearer token123"}],
                        "queryString": [{"name": "page", "value": "2"}],
                        "cookies": [],
                        "resourceType": "document",
                    },
                    "response": {"status": 200},
                },
                {
                    "request": {
                        "method": "GET",
                        "url": "about:blank",
                        "headers": [],
                        "queryString": [],
                        "cookies": [],
                        "resourceType": "document",
                    },
                    "response": {"status": 200},
                },
            ],
        }
    }

    har_path = tmp_path / "test.har"
    with open(har_path, "w") as f:
        json.dump(har_data, f)

    return str(har_path)


@pytest.fixture
def empty_har(tmp_path):
    """Create an empty HAR file with no entries."""
    har_data = {"log": {"version": "1.2", "entries": []}}
    har_path = tmp_path / "empty.har"
    with open(har_path, "w") as f:
        json.dump(har_data, f)
    return str(har_path)


# ─── Tests for _is_media_request ──────────────────────────────────

class TestIsMediaRequest:
    def test_stylesheet_is_media(self):
        assert _is_media_request({"request": {"resourceType": "stylesheet"}}) is True

    def test_image_is_media(self):
        assert _is_media_request({"request": {"resourceType": "image"}}) is True

    def test_font_is_media(self):
        assert _is_media_request({"request": {"resourceType": "font"}}) is True

    def test_media_is_media(self):
        assert _is_media_request({"request": {"resourceType": "media"}}) is True

    def test_xhr_is_not_media(self):
        assert _is_media_request({"request": {"resourceType": "xhr"}}) is False

    def test_document_is_not_media(self):
        assert _is_media_request({"request": {"resourceType": "document"}}) is False

    def test_empty_resource_type_is_not_media(self):
        assert _is_media_request({"request": {"resourceType": ""}}) is False

    def test_missing_resource_type_is_not_media(self):
        assert _is_media_request({"request": {}}) is False


# ─── Tests for _parse_entry ───────────────────────────────────────

class TestParseEntry:
    def test_parse_api_endpoint(self):
        entry = {
            "request": {
                "method": "POST",
                "url": "https://api.example.com/submit",
                "headers": [{"name": "Authorization", "value": "Bearer token"}],
                "queryString": [],
                "cookies": [],
                "resourceType": "xhr",
                "postData": {"mimeType": "application/json", "text": '{}'},
            },
            "response": {"status": 201},
        }
        result = _parse_entry(entry)
        assert result is not None
        assert result["url"] == "https://api.example.com/submit"
        assert result["method"] == "POST"
        assert result["headers"]["Authorization"] == "Bearer token"
        assert result["status"] == 201

    def test_stylesheet_is_filtered(self):
        entry = {
            "request": {
                "method": "GET",
                "url": "https://cdn.example.com/style.css",
                "headers": [],
                "queryString": [],
                "cookies": [],
                "resourceType": "stylesheet",
            },
            "response": {"status": 200},
        }
        assert _parse_entry(entry) is None

    def test_image_is_filtered(self):
        entry = {
            "request": {
                "method": "GET",
                "url": "https://cdn.example.com/img.png",
                "headers": [],
                "queryString": [],
                "cookies": [],
                "resourceType": "image",
            },
            "response": {"status": 200},
        }
        assert _parse_entry(entry) is None

    def test_about_blank_is_filtered(self):
        entry = {
            "request": {
                "method": "GET",
                "url": "about:blank",
                "headers": [],
                "queryString": [],
                "cookies": [],
                "resourceType": "document",
            },
            "response": {"status": 200},
        }
        assert _parse_entry(entry) is None

    def test_data_url_is_filtered(self):
        entry = {
            "request": {
                "method": "GET",
                "url": "data:text/html,hello",
                "headers": [],
                "queryString": [],
                "cookies": [],
                "resourceType": "document",
            },
            "response": {"status": 200},
        }
        assert _parse_entry(entry) is None

    def test_empty_url_is_filtered(self):
        entry = {
            "request": {
                "method": "GET",
                "url": "",
                "headers": [],
                "queryString": [],
                "cookies": [],
                "resourceType": "document",
            },
            "response": {"status": 200},
        }
        assert _parse_entry(entry) is None

    def test_extracts_query_params(self):
        entry = {
            "request": {
                "method": "GET",
                "url": "https://api.example.com/data?page=1&limit=10",
                "headers": [],
                "queryString": [
                    {"name": "page", "value": "1"},
                    {"name": "limit", "value": "10"},
                ],
                "cookies": [],
                "resourceType": "document",
            },
            "response": {"status": 200},
        }
        result = _parse_entry(entry)
        assert result["params"] == {"page": "1", "limit": "10"}


# ─── Tests for parse_har_file ─────────────────────────────────────

class TestParseHarFile:
    def test_parses_endpoints(self, sample_har):
        result = parse_har_file(sample_har)
        assert result["start_url"] == "https://api.example.com/data"
        assert len(result["endpoints"]) == 2
        assert result["total_count"] == 6
        assert result["filtered_count"] == 4

    def test_filtered_count_includes_about_blank(self, sample_har):
        result = parse_har_file(sample_har)
        media_endpoints = [e for e in result["endpoints"]]
        assert len(result["endpoints"]) == 2

    def test_start_url_from_document_page(self, sample_har):
        result = parse_har_file(sample_har)
        assert result["start_url"] == "https://api.example.com/data"

    def test_empty_har(self, empty_har):
        result = parse_har_file(empty_har)
        assert result["start_url"] == ""
        assert result["endpoints"] == []
        assert result["total_count"] == 0
        assert result["filtered_count"] == 0

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            parse_har_file("/nonexistent/path.har")

    def test_dedup_similar_endpoints(self, sample_har):
        result = parse_har_file(sample_har)
        urls = [e["url"] for e in result["endpoints"]]
        assert len(urls) == len(set(urls)), "Should have no duplicate URLs"

    def test_includes_post_method(self, sample_har):
        result = parse_har_file(sample_har)
        methods = [e["method"] for e in result["endpoints"]]
        assert "POST" in methods

    def test_includes_get_method(self, sample_har):
        result = parse_har_file(sample_har)
        methods = [e["method"] for e in result["endpoints"]]
        assert "GET" in methods


# ─── Tests for generate_flow_prompt ──────────────────────────────

class TestGenerateFlowPrompt:
    def test_returns_string(self, sample_har):
        har_summary = parse_har_file(sample_har)
        prompt = generate_flow_prompt(har_summary)
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_contains_required_function_signature(self, sample_har):
        har_summary = parse_har_file(sample_har)
        prompt = generate_flow_prompt(har_summary)
        assert "def execute(ctx: dict, inputs: dict) -> dict:" in prompt

    def test_contains_httpx_guidance(self, sample_har):
        har_summary = parse_har_file(sample_har)
        prompt = generate_flow_prompt(har_summary)
        assert "httpx" in prompt.lower()

    def test_contains_success_schema(self, sample_har):
        har_summary = parse_har_file(sample_har)
        prompt = generate_flow_prompt(har_summary)
        assert '"success": bool' in prompt or "success" in prompt

    def test_contains_start_url(self, sample_har):
        har_summary = parse_har_file(sample_har)
        prompt = generate_flow_prompt(har_summary)
        assert "https://api.example.com/data" in prompt

    def test_contains_endpoint_info(self, sample_har):
        har_summary = parse_har_file(sample_har)
        prompt = generate_flow_prompt(har_summary)
        assert "POST" in prompt or "GET" in prompt

    def test_empty_har_prompt(self, empty_har):
        har_summary = parse_har_file(empty_har)
        prompt = generate_flow_prompt(har_summary)
        assert isinstance(prompt, str)
        assert "0" in prompt or "zero" in prompt.lower()
