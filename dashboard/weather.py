"""Open-Meteo location lookup and short-term forecast interpretation."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class WeatherError(RuntimeError):
    """A weather lookup failed or returned incomplete data."""


TIMEOUT_SECONDS = 12


def _get_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "AnkoDisplayWeatherDashboard/0.1"})
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise WeatherError(f"Weather service request failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise WeatherError("Weather service returned an unexpected response")
    if payload.get("error"):
        raise WeatherError(str(payload.get("reason", "Weather service error")))
    return payload


def geocode(location: str, country_code: str) -> tuple[float, float, str]:
    query = urlencode({"name": location, "count": 10, "language": "en", "countryCode": country_code})
    data = _get_json(f"https://geocoding-api.open-meteo.com/v1/search?{query}")
    results = data.get("results") or []
    if not results:
        raise WeatherError(f"No matching location found for {location!r} in {country_code}")
    match = results[0]
    try:
        latitude, longitude = float(match["latitude"]), float(match["longitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherError("Location lookup did not include valid coordinates") from exc
    label = ", ".join(str(match[key]) for key in ("name", "admin1", "country") if match.get(key))
    return latitude, longitude, label


def fetch_forecast(latitude: float, longitude: float, timezone: str = "auto", forecast_hours: int = 2) -> dict:
    params = urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "weather_code,precipitation_probability",
            "forecast_hours": max(1, forecast_hours),
            "timezone": timezone,
        }
    )
    data = _get_json(f"https://api.open-meteo.com/v1/forecast?{params}")
    hourly = data.get("hourly")
    if not isinstance(hourly, dict) or not hourly.get("time") or not hourly.get("weather_code"):
        raise WeatherError("Forecast response is missing hourly weather data")
    return data


def select_condition(forecast: dict, window_hours: int, rain_probability_threshold: int) -> str:
    """Choose the most actionable condition in the upcoming forecast window."""
    hourly = forecast.get("hourly", {})
    codes = hourly.get("weather_code", [])
    probabilities = hourly.get("precipitation_probability", [])
    count = min(max(1, window_hours), len(codes))
    if count == 0:
        raise WeatherError("Forecast has no weather-code values")

    selected_codes = []
    for index in range(count):
        try:
            code = int(codes[index])
        except (TypeError, ValueError) as exc:
            raise WeatherError("Forecast contains an invalid weather code") from exc
        selected_codes.append(code)
        if code >= 95:
            return "thunderstorm"

    for index, code in enumerate(selected_codes):
        if code in {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}:
            return "rain"
        if index < len(probabilities) and probabilities[index] is not None:
            try:
                if float(probabilities[index]) >= rain_probability_threshold:
                    return "rain"
            except (TypeError, ValueError) as exc:
                raise WeatherError("Forecast contains an invalid precipitation probability") from exc

    if any(code not in {0, 1} for code in selected_codes):
        return "cloudy"
    return "sunny"
