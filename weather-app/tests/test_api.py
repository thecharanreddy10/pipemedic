import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("WEATHER_MODE", "mock")
    monkeypatch.setenv("APP_VERSION", "test")
    import app.main
    return TestClient(importlib.reload(app.main).app)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "mode": "mock", "version": "test"}


def test_weather_for_a_known_city(client):
    r = client.get("/api/weather", params={"city": "Hyderabad"})
    assert r.status_code == 200
    assert r.json()["temperature_f"] == 88.5


def test_an_unknown_city_is_a_404(client):
    assert client.get("/api/weather", params={"city": "Atlantis"}).status_code == 404


def test_the_page_shows_the_default_city(client):
    r = client.get("/")
    assert r.status_code == 200 and "Hyderabad" in r.text and "sample data" in r.text


def test_the_app_refuses_to_start_without_a_mode(monkeypatch):
    monkeypatch.delenv("WEATHER_MODE", raising=False)
    from app import config
    with pytest.raises(RuntimeError, match="WEATHER_MODE"):
        config.load()


def test_the_weather_service_not_answering_is_a_502_not_a_crash(client, monkeypatch):
    import httpx

    import app.main

    def down(city):
        raise httpx.ConnectTimeout("timed out")
    monkeypatch.setattr(app.main.client, "current", down)
    r = client.get("/api/weather", params={"city": "London"})
    assert r.status_code == 502 and "did not answer" in r.json()["detail"]
    assert "did not answer" in client.get("/").text
