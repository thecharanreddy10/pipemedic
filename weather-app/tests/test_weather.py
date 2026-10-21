import pytest

from app.weather import CityNotFound, WeatherClient, c_to_f, describe, parse_forecast


@pytest.mark.parametrize("c, f", [(0, 32.0), (100, 212.0), (-40, -40.0), (31.4, 88.5)])
def test_celsius_to_fahrenheit(c, f):
    assert c_to_f(c) == f


def test_weather_codes_are_described():
    assert describe(0) == "Clear sky"
    assert describe(95) == "Thunderstorm"
    assert describe(12345) == "Unknown"


def test_open_meteo_answers_become_a_report():
    place = {"name": "Oslo", "country": "Norway", "latitude": 59.9, "longitude": 10.7}
    forecast = {"current": {"temperature_2m": -3.04, "wind_speed_10m": 7.61, "weather_code": 71}}
    assert parse_forecast(place, forecast) == {"city": "Oslo", "country": "Norway", "temperature_c": -3.0, "temperature_f": 26.5,
                                                "wind_kmh": 7.6, "conditions": "Light snow"}


def test_mock_mode_knows_its_sample_cities_and_nothing_else():
    client = WeatherClient("mock")
    assert client.current(" London ")["conditions"] == "Light rain"
    with pytest.raises(CityNotFound):
        client.current("Atlantis")
