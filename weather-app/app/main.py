"""Weather app: a small page and a JSON API for the current weather in a city."""
import html

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from app import config
from app.weather import CityNotFound, WeatherClient

settings = config.load()
client = WeatherClient(settings.mode, settings.timeout_s)
app = FastAPI(title="Weather app", version=settings.version)

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Weather</title><style>
body{{font-family:system-ui,Segoe UI,sans-serif;margin:0;background:#eef3f8;color:#13202e}}
main{{max-width:520px;margin:48px auto;padding:0 16px}}
form{{display:flex;gap:8px}}input{{flex:1;font-size:16px;padding:9px 12px;border:1px solid #b9c6d6;border-radius:6px}}
button{{font-size:16px;padding:9px 16px;border:0;border-radius:6px;background:#1d5fa8;color:#fff;cursor:pointer}}
.card{{background:#fff;border:1px solid #d5dee9;border-radius:10px;padding:20px 22px;margin-top:18px}}
.temp{{font-size:44px;font-weight:650}}.muted{{color:#5a6b7e}}
</style></head><body><main><h1>Weather</h1>
<form method="get" action="/"><label for="city" hidden>City</label><input id="city" name="city" value="{city}" placeholder="City">
<button type="submit">Show</button></form>{body}<p class="muted">Version {version} &middot; data: {source}</p></main></body></html>"""


@app.get("/health")
def health():
    return {"status": "ok", "mode": settings.mode, "version": settings.version}


@app.get("/api/weather")
def weather(city: str = Query(min_length=1, max_length=80)):
    try:
        return client.current(city)
    except CityNotFound:
        raise HTTPException(status_code=404, detail=f"No city called {city!r} was found")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="The weather service did not answer; try again shortly")


@app.get("/", response_class=HTMLResponse)
def page(city: str = ""):
    city = city or settings.default_city
    try:
        w = client.current(city)
        body = (f'<div class="card"><div class="muted">{html.escape(w["city"])}, {html.escape(w["country"])}</div>'
                f'<div class="temp">{w["temperature_c"]}&deg;C <span class="muted" style="font-size:20px">{w["temperature_f"]}&deg;F</span></div>'
                f'<div>{html.escape(w["conditions"])} &middot; wind {w["wind_kmh"]} km/h</div></div>')
    except CityNotFound:
        body = f'<div class="card">No city called {html.escape(city)} was found.</div>'
    except httpx.HTTPError:
        body = '<div class="card">The weather service did not answer. Try again shortly.</div>'
    source = "sample data" if settings.mode == "mock" else "Open-Meteo"
    return PAGE.format(city=html.escape(city), body=body, version=html.escape(settings.version), source=source)
