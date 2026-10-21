FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --disable-pip-version-check -r requirements.txt
COPY app ./app

ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION} WEATHER_MODE=live DEFAULT_CITY=Hyderabad
RUN useradd --uid 10001 --no-create-home weather
USER weather
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
