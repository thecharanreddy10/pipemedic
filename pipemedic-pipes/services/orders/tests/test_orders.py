from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def _ok():
    m = MagicMock()
    m.raise_for_status.return_value = None
    return m


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_create_and_get_order():
    with patch("app.main.httpx.post", return_value=_ok()):
        r = client.post("/orders", params={"item": "laptop", "qty": 1, "amount": 999.0})
    assert r.status_code == 200
    oid = r.json()["id"]
    assert client.get(f"/orders/{oid}").json()["status"] == "confirmed"


def test_missing_order():
    assert client.get("/orders/nope").status_code == 404
