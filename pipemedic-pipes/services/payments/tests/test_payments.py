from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_pay_ok():
    r = client.post("/pay", params={"order_id": "o1", "amount": 19.99})
    assert r.status_code == 200 and r.json()["status"] == "approved"


def test_pay_rejects_non_positive():
    assert client.post("/pay", params={"order_id": "o1", "amount": 0}).status_code == 400
