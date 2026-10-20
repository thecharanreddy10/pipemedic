import pytest
from fastapi.testclient import TestClient
from app import main

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def reset_stock():
    main.STOCK.update({"laptop": 10, "phone": 25, "headphones": 40})


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_list_items():
    assert client.get("/items").json()["laptop"] == 10


def test_reserve_reduces_stock():
    r = client.post("/reserve/laptop/3")
    assert r.status_code == 200
    assert r.json()["remaining"] == 7  # fails: service returns 8


def test_reserve_unknown_item():
    assert client.post("/reserve/toaster/1").status_code == 404


def test_reserve_too_many():
    assert client.post("/reserve/laptop/99").status_code == 409
