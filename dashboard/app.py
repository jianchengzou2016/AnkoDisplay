"""Weather and route 150/153 pages for the Anko 16x16 display."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, time as clock_time
from pathlib import Path

from bus import BusArrival, BusDataError, TranslinkBusClient, sample_arrival
from render import render_bus_status, render_icon
from weather import WeatherError, fetch_forecast, geocode, select_condition


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
DEFAULT_CONFIG = ROOT / "config.json"
LOG = logging.getLogger("ankodashboard")
WEATHER_CONDITIONS = ("sunny", "cloudy", "rain", "thunderstorm")
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def load_config(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read configuration file {path}: {exc}") from exc

    required = ("location", "country_code", "display_address", "client_directory")
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise ValueError("Missing configuration value(s): " + ", ".join(missing))
    for key, default in (("bus_refresh_seconds", 60), ("weather_refresh_seconds", 300),
                         ("page_cycle_seconds", 300), ("weather_page_seconds", 10)):
        value = int(config.get(key, default))
        if value < 1 or (key == "weather_page_seconds" and value >= int(config.get("page_cycle_seconds", 300))):
            raise ValueError(f"{key} has an invalid value")
    if not 0 <= int(config.get("rain_probability_threshold", 50)) <= 100:
        raise ValueError("rain_probability_threshold must be between 0 and 100")
    if not 1 <= int(config.get("forecast_window_hours", 2)) <= 24:
        raise ValueError("forecast_window_hours must be between 1 and 24")
    for key in ("bus_inbound_stop_id", "bus_outbound_stop_id"):
        if not str(config.get(key, "")).isdigit():
            raise ValueError(f"{key} must contain a numeric Translink stop ID")
    routes = config.get("bus_routes", [config.get("bus_route", "150")])
    if not isinstance(routes, list) or not routes or any(not str(route).isdigit() for route in routes):
        raise ValueError("bus_routes must be a non-empty list of route numbers")
    log_level = str(config.get("log_level", "INFO")).upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("log_level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
    route_prefixes = config.get("realtime_route_id_prefixes", {})
    if not isinstance(route_prefixes, dict):
        raise ValueError("realtime_route_id_prefixes must be an object keyed by passenger route number")
    for route, prefixes in route_prefixes.items():
        if not str(route).isdigit() or not isinstance(prefixes, list) or not prefixes or any(
            not isinstance(prefix, str) or not prefix for prefix in prefixes
        ):
            raise ValueError("realtime_route_id_prefixes values must be non-empty lists of route ID prefixes")
    if int(config.get("bus_alternate_window_minutes", 60)) < 1:
        raise ValueError("bus_alternate_window_minutes must be at least 1")
    active_hours = config.get("active_hours", {"enabled": False})
    if not isinstance(active_hours, dict):
        raise ValueError("active_hours must be an object")
    if not isinstance(active_hours.get("enabled", False), bool):
        raise ValueError("active_hours.enabled must be true or false")
    if active_hours.get("enabled", False):
        windows = active_hours.get("windows")
        if not isinstance(windows, list) or not windows:
            raise ValueError("active_hours.windows must be a non-empty list when active hours are enabled")
        for index, window in enumerate(windows):
            if not isinstance(window, dict):
                raise ValueError(f"active_hours.windows[{index}] must be an object")
            for field in ("start", "end"):
                value = window.get(field)
                try:
                    parsed = datetime.strptime(value, "%H:%M").time()
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"active_hours.windows[{index}].{field} must use HH:MM (24-hour) format") from exc
                if parsed.strftime("%H:%M") != value:
                    raise ValueError(f"active_hours.windows[{index}].{field} must use zero-padded HH:MM format")
            days = window.get("days", list(WEEKDAYS))
            if not isinstance(days, list) or not days or any(day not in WEEKDAYS for day in days):
                raise ValueError(
                    f"active_hours.windows[{index}].days must be a non-empty list of mon, tue, wed, thu, fri, sat, sun"
                )
            if window["start"] == window["end"]:
                raise ValueError(f"active_hours.windows[{index}] start and end must differ")
    return config


def _active_windows(config: dict) -> list[tuple[clock_time, clock_time, frozenset[int]]]:
    schedule = config.get("active_hours", {})
    return [
        (
            datetime.strptime(window["start"], "%H:%M").time(),
            datetime.strptime(window["end"], "%H:%M").time(),
            frozenset(WEEKDAYS[day] for day in window.get("days", WEEKDAYS)),
        )
        for window in schedule.get("windows", [])
    ]


def _is_in_active_hours(now: datetime, windows: list[tuple[clock_time, clock_time, frozenset[int]]]) -> bool:
    local_time = now.time().replace(tzinfo=None)
    weekday = now.weekday()
    for start, end, days in windows:
        if start < end:
            if weekday in days and start <= local_time < end:
                return True
        elif (weekday in days and local_time >= start) or (
            (weekday - 1) % 7 in days and local_time < end
        ):
            return True
    return False


def _client_command(config: dict, *arguments: str) -> tuple[list[str], Path]:
    client_dir = Path(config["client_directory"])
    if not client_dir.is_absolute():
        client_dir = (PROJECT_ROOT / client_dir).resolve()
    client_app = client_dir / "app.py"
    if not client_app.is_file():
        raise FileNotFoundError(f"iDotMatrix client app.py not found: {client_app}")

    configured_python = Path(str(config.get("client_python", "python")))
    if not configured_python.is_absolute() and ("/" in str(configured_python) or "\\" in str(configured_python)):
        configured_python = (PROJECT_ROOT / configured_python).resolve()
    command = [str(configured_python), str(client_app), "--address", str(config["display_address"]), *arguments]
    return command, client_dir


def upload_image(image_path: Path, config: dict, content_description: str) -> None:
    command, client_dir = _client_command(
        config, "--image", "true", "--set-image", str(image_path.resolve())
    )
    LOG.info(
        "Uploading to display %s: %s (image=%s)",
        config["display_address"], content_description, image_path.name,
    )
    subprocess.run(command, cwd=client_dir, check=True)


def set_display_screen(config: dict, state: str) -> None:
    command, client_dir = _client_command(config, "--screen", state)
    LOG.info("Turning display screen %s", state)
    subprocess.run(command, cwd=client_dir, check=True)


def fetch_weather_condition(config: dict, location: tuple[float, float, str] | None = None) -> str:
    latitude, longitude, place = location or geocode(config["location"], config["country_code"])
    forecast = fetch_forecast(
        latitude, longitude,
        timezone=config.get("timezone", "auto"),
        forecast_hours=int(config.get("forecast_window_hours", 2)),
    )
    condition = select_condition(
        forecast,
        window_hours=int(config.get("forecast_window_hours", 2)),
        rain_probability_threshold=int(config.get("rain_probability_threshold", 50)),
    )
    LOG.info("Forecast for %s: %s", place, condition)
    return condition


def _weather_image(condition: str) -> Path:
    path = ROOT / "output" / f"weather_{condition}.png"
    render_icon(condition, path)
    return path


def _bus_image(arrival: BusArrival) -> Path:
    path = ROOT / "output" / "bus_status.png"
    render_bus_status(arrival, path)
    eta = arrival.eta_minutes
    eta_text = "no service found" if eta is None else f"{eta} min"
    LOG.info("Bus %s %s: %s (%s)", arrival.route, arrival.direction, eta_text, arrival.source)
    return path


def _arrival_signature(arrival: BusArrival) -> tuple:
    return arrival.route, arrival.direction, arrival.eta_minutes, arrival.source


def _upload_content_description(signature: tuple) -> str:
    page_type, page_data = signature
    if page_type == "weather":
        return f"weather icon={page_data}; source=Open-Meteo forecast"

    if page_data is None:
        return "bus page content=unavailable; source=unknown"

    route, direction, eta, source = page_data
    destination_marker = "C" if direction == "inbound" else "B"
    eta_text = "--m" if eta is None else ("99+m" if eta >= 100 else f"{eta}m")
    source_description = {
        "realtime": "Translink realtime TripUpdates API prediction",
        "scheduled": "Translink GTFS timetable",
        "unknown": "unknown/no arrival data",
    }.get(source, str(source))
    return (
        f"bus page content={route} {destination_marker} {eta_text}; "
        f"source={source_description}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Weather and route 150/153 dashboard for a 16x16 Anko display")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="JSON settings file")
    parser.add_argument("--sample", choices=WEATHER_CONDITIONS, help="Render an offline weather sample")
    parser.add_argument("--sample-bus", action="store_true", help="Render an offline sample bus page")
    parser.add_argument("--once", action="store_true", help="Fetch each data source once and render one update")
    parser.add_argument("--upload", action="store_true", help="Upload changed pages over Bluetooth")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = parse_args()
    try:
        config = load_config(args.config)
        logging.getLogger().setLevel(getattr(logging, config.get("log_level", "INFO").upper()))
        schedule_enabled = bool(config.get("active_hours", {}).get("enabled", False))
        if args.sample:
            path = _weather_image(args.sample)
            LOG.info("Saved offline weather sample %s", path)
            return 0
        if args.sample_bus:
            path = _bus_image(sample_arrival())
            LOG.info("Saved offline bus sample %s", path)
            return 0
        if schedule_enabled and not args.upload:
            LOG.warning("Active-hours display control requires --upload; schedule is ignored")
            schedule_enabled = False
        active_windows = _active_windows(config) if schedule_enabled else []

        bus_client: TranslinkBusClient | None = None
        last_bus_image: Path | None = None
        last_weather_image: Path | None = None
        last_bus_signature: tuple | None = None
        active_bus_route: str | None = None
        last_weather_condition: str | None = None
        weather_location: tuple[float, float, str] | None = None
        displayed_signature: tuple | None = None
        last_upload_attempt = 0.0
        started = time.monotonic()
        next_bus_update = 0.0
        next_weather_update = 0.0
        schedule_was_active = False
        display_is_on = False
        last_screen_attempt = 0.0
        waiting_logged = False

        while True:
            now = time.monotonic()
            if schedule_enabled:
                active_now = _is_in_active_hours(datetime.now().astimezone(), active_windows)
                if not active_now:
                    if schedule_was_active:
                        LOG.info("Active hours ended; turning the display off and stopping")
                        try:
                            set_display_screen(config, "off")
                        except (OSError, subprocess.SubprocessError, ValueError) as exc:
                            LOG.error("Could not turn the display off: %s", exc)
                        return 0
                    if not waiting_logged:
                        LOG.info("Outside active hours; waiting for the next configured window")
                        waiting_logged = True
                    if args.once:
                        return 0
                    time.sleep(max(0.5, float(config.get("loop_sleep_seconds", 1))))
                    continue

                if not schedule_was_active:
                    LOG.info("Active hours started; turning the display on before content updates")
                    schedule_was_active = True
                    displayed_signature = None
                    waiting_logged = False
                if not display_is_on and now - last_screen_attempt >= 15:
                    last_screen_attempt = now
                    try:
                        set_display_screen(config, "on")
                        display_is_on = True
                        displayed_signature = None
                    except (OSError, subprocess.SubprocessError, ValueError) as exc:
                        LOG.error("Could not turn the display on; will retry: %s", exc)

            if now >= next_bus_update:
                try:
                    if bus_client is None:
                        bus_client = TranslinkBusClient(config, ROOT)
                    arrivals = bus_client.next_arrivals()
                    upcoming = [arrival for arrival in arrivals if arrival.arrival is not None]
                    alternate_window = int(config.get("bus_alternate_window_minutes", 60))
                    alternating = [
                        arrival for arrival in upcoming
                        if arrival.eta_minutes is not None and arrival.eta_minutes <= alternate_window
                    ]

                    if len(alternating) > 1:
                        routes = [arrival.route for arrival in alternating]
                        if active_bus_route in routes:
                            next_index = (routes.index(active_bus_route) + 1) % len(routes)
                            arrival = alternating[next_index]
                        else:
                            arrival = min(alternating, key=lambda item: item.arrival)
                    elif upcoming:
                        arrival = min(upcoming, key=lambda item: item.arrival)
                    else:
                        arrival = arrivals[0]

                    active_bus_route = arrival.route
                    last_bus_image = _bus_image(arrival)
                    last_bus_signature = _arrival_signature(arrival)
                except (BusDataError, OSError, ValueError) as exc:
                    LOG.error("Bus update failed: %s", exc)
                next_bus_update = now + int(config.get("bus_refresh_seconds", 60))

            if now >= next_weather_update:
                try:
                    if weather_location is None:
                        weather_location = geocode(config["location"], config["country_code"])
                    condition = fetch_weather_condition(config, weather_location)
                    if condition != last_weather_condition or last_weather_image is None:
                        last_weather_image = _weather_image(condition)
                    last_weather_condition = condition
                except (WeatherError, OSError, ValueError) as exc:
                    LOG.error("Weather update failed: %s", exc)
                next_weather_update = now + int(config.get("weather_refresh_seconds", 300))

            elapsed = time.monotonic() - started
            cycle = int(config.get("page_cycle_seconds", 300))
            weather_duration = int(config.get("weather_page_seconds", 10))
            phase = elapsed % cycle
            show_weather = elapsed >= cycle and phase < weather_duration and last_weather_image is not None
            if show_weather:
                desired_image = last_weather_image
                signature = ("weather", last_weather_condition)
            else:
                desired_image = last_bus_image
                signature = ("bus", last_bus_signature)

            if (args.upload and (not schedule_enabled or display_is_on)
                    and desired_image is not None and signature != displayed_signature
                    and now - last_upload_attempt >= 15):
                last_upload_attempt = now
                try:
                    content_description = _upload_content_description(signature)
                    upload_image(desired_image, config, content_description)
                    displayed_signature = signature
                except (OSError, subprocess.SubprocessError, ValueError) as exc:
                    LOG.error("Display upload failed: %s", exc)

            if args.once:
                return 0
            time.sleep(max(0.5, float(config.get("loop_sleep_seconds", 1))))
    except (OSError, ValueError) as exc:
        LOG.error("Dashboard configuration error: %s", exc)
        return 2
    except KeyboardInterrupt:
        LOG.info("Stopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())
