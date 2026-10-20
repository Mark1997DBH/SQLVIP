from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


client = TestClient(app)


def test_index_page() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "SQLVIP" in response.text
    assert "source-input" in response.text


def test_health() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "version": "1.0.0", "service": "SQLVIP"}


def test_dialect_catalog_has_five_databases() -> None:
    response = client.get("/api/dialects")
    assert response.status_code == 200
    keys = {item["key"] for item in response.json()["dialects"]}
    assert keys == {"db2", "postgres", "oracle", "mysql", "dameng"}


def test_convert_endpoint() -> None:
    response = client.post(
        "/api/convert",
        json={
            "sql": "SELECT * FROM users LIMIT 5;",
            "source": "mysql",
            "target": "oracle",
            "pretty": True,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert "FETCH FIRST 5 ROWS ONLY" in payload["sql"]
    assert payload["statement_count"] == 1


def test_convert_endpoint_parse_error() -> None:
    response = client.post(
        "/api/convert",
        json={
            "sql": "SELECT FROM WHERE",
            "source": "mysql",
            "target": "oracle",
            "pretty": True,
        },
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "PARSE_ERROR"
    assert error["line"] == 1


def test_convert_endpoint_rejects_unknown_dialect() -> None:
    response = client.post(
        "/api/convert",
        json={
            "sql": "SELECT 1",
            "source": "sqlite",
            "target": "oracle",
        },
    )
    assert response.status_code == 422
