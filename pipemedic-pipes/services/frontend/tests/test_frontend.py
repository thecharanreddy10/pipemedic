from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_home_page():
    r = client.get("/")
    assert r.status_code == 200 and "PipeMedic Shop" in r.text


def test_catalog_proxies_inventory():
    m = MagicMock()
    m.json.return_value = {"laptop": 10}
    with patch("app.main.httpx.get", return_value=m):
        assert client.get("/api/catalog").json() == {"laptop": 10}
