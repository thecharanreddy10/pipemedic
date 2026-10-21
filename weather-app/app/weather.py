"""Current weather for a city: from Open-Meteo (no API key needed), or from built-in sample data in mock mode."""
import httpx

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes, as used by Open-Meteo
CONDITIONS = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 80: "Rain showers", 81: "Heavy rain showers", 82: "Violent rain showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
}

SAMPLE = {
    "hyderabad": {"city": "Hyderabad", "country": "India", "temperature_c": 31.4, "wind_kmh": 11.2, "code": 2},
    "london": {"city": "London", "country": "United Kingdom", "temperature_c": 12.0, "wind_kmh": 19.8, "code": 61},
    "new york": {"city": "New York", "country": "United States", "temperature_c": 18.5, "wind_kmh": 14.0, "code": 1},
    "oslo": {"city": "Oslo", "country": "Norway", "temperature_c": -3.0, "wind_kmh": 7.6, "code": 71},
}


class CityNotFound(Exception):
    pass


def c_to_f(celsius: float) -> float:
    """Celsius to Fahrenheit, rounded to one decimal."""
    return round(celsius * 9 / 5 + 32, 1)


def describe(code: int) -> str:
    return CONDITIONS.get(code, "Unknown")


def report(city: str, country: str, temperature_c: float, wind_kmh: float, code: int) -> dict:
    return {"city": city, "country": country, "temperature_c": round(temperature_c, 1), "temperature_f": c_to_f(temperature_c),
            "wind_kmh": round(wind_kmh, 1), "conditions": describe(code)}


def parse_forecast(place: dict, forecast: dict) -> dict:
    """Turn Open-Meteo's geocoding and forecast answers into a report."""
    now = forecast["current"]
    return report(place["name"], place.get("country", ""), now["temperature_2m"], now["wind_speed_10m"], now["weather_code"])


class WeatherClient:
    def __init__(self, mode: str, timeout_s: float = 5.0):
        self.mode, self.timeout_s = mode, timeout_s

    def current(self, city: str) -> dict:
        if self.mode == "mock":
            sample = SAMPLE.get(city.strip().lower())
            if not sample:
                raise CityNotFound(city)
            return report(**sample)
        with httpx.Client(timeout=self.timeout_s) as http:
            places = http.get(GEOCODE_URL, params={"name": city, "count": 1}).raise_for_status().json().get("results") or []
            if not places:
                raise CityNotFound(city)
            place = places[0]
            forecast = http.get(FORECAST_URL, params={"latitude": place["latitude"], "longitude": place["longitude"],
                                                      "current": "temperature_2m,wind_speed_10m,weather_code"}).raise_for_status().json()
        return parse_forecast(place, forecast)
