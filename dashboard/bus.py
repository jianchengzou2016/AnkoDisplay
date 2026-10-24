"""Configurable Translink GTFS/GTFS-Realtime route and stop lookup."""

from __future__ import annotations

import csv
import io
import logging
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time as day_time, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


LOG = logging.getLogger("ankodashboard.bus")
TRANSIT_TZ = ZoneInfo("Australia/Brisbane")
DEFAULT_GTFS_URL = (
    "https://www.data.qld.gov.au/dataset/1c4c0295-135f-48d8-bc26-7391ffc5fd70/"
    "resource/e43b6b9f-fc2b-4630-a7c9-86dd5483552b/archive/SEQ_GTFS.zip"
)
DEFAULT_TRIP_UPDATES_URL = "https://gtfsrt.api.translink.com.au/api/realtime/SEQ/TripUpdates/Bus"


class BusDataError(RuntimeError):
    """A schedule or realtime bus lookup failed."""


@dataclass(frozen=True)
class ScheduledStopTime:
    trip_id: str
    route: str
    service_id: str
    stop_id: str
    stop_sequence: int
    seconds_after_midnight: int


@dataclass(frozen=True)
class BusArrival:
    route: str
    direction: str
    destination: str
    stop_id: str
    arrival: datetime | None
    source: str

    @property
    def eta_minutes(self) -> int | None:
        if self.arrival is None:
            return None
        seconds = (self.arrival - datetime.now(TRANSIT_TZ)).total_seconds()
        return max(0, int((seconds + 59) // 60))


def _download(url: str, timeout: int = 30) -> bytes:
    request = Request(url, headers={"User-Agent": "AnkoDisplayDashboard/0.2"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise BusDataError(f"Could not download Translink data: {exc}") from exc


def ensure_schedule_file(config: dict, dashboard_dir: Path) -> Path:
    cache_path = Path(config.get("gtfs_cache_file", dashboard_dir / "cache" / "SEQ_GTFS.zip"))
    if not cache_path.is_absolute():
        cache_path = (dashboard_dir.parent / cache_path).resolve()
    max_age = int(config.get("gtfs_cache_days", 14))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists() and time.time() - cache_path.stat().st_mtime < max_age * 86400:
        return cache_path

    url = str(config.get("gtfs_schedule_url", DEFAULT_GTFS_URL))
    LOG.info("Downloading the Translink SEQ timetable (first run or cache refresh)")
    contents = _download(url, timeout=int(config.get("bus_download_timeout_seconds", 90)))
    temporary_path = cache_path.with_suffix(cache_path.suffix + ".part")
    try:
        with temporary_path.open("wb") as handle:
            handle.write(contents)
        with zipfile.ZipFile(temporary_path) as archive:
            if "routes.txt" not in archive.namelist() or "stop_times.txt" not in archive.namelist():
                raise BusDataError("Translink schedule ZIP is missing required GTFS files")
        temporary_path.replace(cache_path)
    except (OSError, zipfile.BadZipFile) as exc:
        temporary_path.unlink(missing_ok=True)
        raise BusDataError(f"Translink schedule data is not a valid GTFS ZIP: {exc}") from exc
    return cache_path


def _csv_rows(archive: zipfile.ZipFile, filename: str):
    try:
        text = io.TextIOWrapper(archive.open(filename), encoding="utf-8-sig", newline="")
    except KeyError:
        return
    yield from csv.DictReader(text)


def _parse_gtfs_time(value: str) -> int:
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError(f"Invalid GTFS time: {value}")
    hours, minutes, seconds = (int(part) for part in parts)
    return hours * 3600 + minutes * 60 + seconds


class TranslinkBusClient:
    """Loads the static GTFS subset once, then refreshes trip updates on demand."""

    def __init__(self, config: dict, dashboard_dir: Path):
        self.config = config
        self.timezone = ZoneInfo(str(config.get("bus_timezone", "Australia/Brisbane")))
        configured_routes = config.get("bus_routes", [config.get("bus_route", "150")])
        if isinstance(configured_routes, str):
            configured_routes = [configured_routes]
        self.route_short_names = tuple(dict.fromkeys(str(route) for route in configured_routes))
        if not self.route_short_names:
            raise BusDataError("At least one bus route must be configured")
        self.route_short_name = self.route_short_names[0]
        self.stop_ids = {
            "inbound": str(config.get("bus_inbound_stop_id", "000000")),
            "outbound": str(config.get("bus_outbound_stop_id", "000000")),
        }
        # Translink's public stop codes may differ from the GTFS stop_id values
        # used in stop_times and TripUpdates; resolve them through stops.txt.
        self.gtfs_stop_ids: dict[str, str] = {}
        self.destinations = {
            "inbound": str(config.get("bus_inbound_destination", "Inbound")),
            "outbound": str(config.get("bus_outbound_destination", "Outbound")),
        }
        schedule_path = ensure_schedule_file(config, dashboard_dir)
        (self.route_ids, self.trip_services, self.trip_routes, self.stop_times,
         self.calendars, self.exceptions) = (
            self._read_static_schedule(schedule_path)
        )
        if not self.route_ids:
            raise BusDataError(
                f"None of routes {', '.join(self.route_short_names)} were found in the Translink timetable"
            )
        if not self.stop_times:
            raise BusDataError("No timetable entries were found for the configured routes and stop IDs")
        LOG.info(
            "Resolved stop codes %s to GTFS stop IDs %s",
            self.stop_ids,
            self.gtfs_stop_ids,
        )
        LOG.info(
            "Loaded routes %s timetable: %d route IDs, %d stop times",
            ", ".join(self.route_short_names),
            len(self.route_ids),
            len(self.stop_times),
        )

    def _read_static_schedule(self, path: Path):
        route_ids: set[str] = set()
        trip_services: dict[str, str] = {}
        trip_routes: dict[str, str] = {}
        stop_times: list[ScheduledStopTime] = []
        calendars: dict[str, dict] = {}
        exceptions: dict[tuple[str, str], int] = {}

        try:
            with zipfile.ZipFile(path) as archive:
                stop_ids_by_code = {
                    row.get("stop_code", "").strip(): row.get("stop_id", "").strip()
                    for row in _csv_rows(archive, "stops.txt")
                    if row.get("stop_code") and row.get("stop_id")
                }
                known_gtfs_stop_ids = set(stop_ids_by_code.values())
                unknown_stops = [
                    public_code for public_code in self.stop_ids.values()
                    if public_code not in stop_ids_by_code and public_code not in known_gtfs_stop_ids
                ]
                if unknown_stops:
                    raise BusDataError(
                        "Configured Translink stop code(s) not found in stops.txt: "
                        + ", ".join(unknown_stops)
                    )
                self.gtfs_stop_ids = {
                    direction: stop_ids_by_code.get(public_code, public_code)
                    for direction, public_code in self.stop_ids.items()
                }
                needed_stops = set(self.gtfs_stop_ids.values())

                for row in _csv_rows(archive, "routes.txt"):
                    if row.get("route_short_name", "").strip() in self.route_short_names:
                        route_ids.add(row["route_id"])
                if not route_ids:
                    return route_ids, trip_services, trip_routes, stop_times, calendars, exceptions

                for row in _csv_rows(archive, "trips.txt"):
                    if row.get("route_id") in route_ids:
                        trip_services[row["trip_id"]] = row.get("service_id", "")
                        trip_routes[row["trip_id"]] = row["route_id"]

                route_short_names_by_id = {
                    row["route_id"]: row.get("route_short_name", "").strip()
                    for row in _csv_rows(archive, "routes.txt")
                    if row.get("route_id") in route_ids
                }

                for row in _csv_rows(archive, "stop_times.txt"):
                    trip_id = row.get("trip_id", "")
                    stop_id = row.get("stop_id", "")
                    if trip_id not in trip_services or stop_id not in needed_stops:
                        continue
                    raw_time = row.get("arrival_time") or row.get("departure_time")
                    if not raw_time:
                        continue
                    stop_times.append(
                        ScheduledStopTime(
                            trip_id=trip_id,
                            route=route_short_names_by_id[trip_routes[trip_id]],
                            service_id=trip_services[trip_id],
                            stop_id=stop_id,
                            stop_sequence=int(row.get("stop_sequence", "0")),
                            seconds_after_midnight=_parse_gtfs_time(raw_time),
                        )
                    )

                for row in _csv_rows(archive, "calendar.txt"):
                    calendars[row["service_id"]] = row
                for row in _csv_rows(archive, "calendar_dates.txt"):
                    exceptions[(row["service_id"], row["date"])] = int(row["exception_type"])
        except (OSError, zipfile.BadZipFile, KeyError, ValueError) as exc:
            raise BusDataError(f"Could not read Translink schedule data: {exc}") from exc
        return route_ids, trip_services, trip_routes, stop_times, calendars, exceptions

    def _service_active(self, service_id: str, service_date: date) -> bool:
        date_key = service_date.strftime("%Y%m%d")
        exception = self.exceptions.get((service_id, date_key))
        if exception == 1:
            return True
        if exception == 2:
            return False
        rule = self.calendars.get(service_id)
        if not rule:
            return False
        if not rule["start_date"] <= date_key <= rule["end_date"]:
            return False
        day_field = (
            "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
        )[service_date.weekday()]
        return rule.get(day_field) == "1"

    def _scheduled_departures(self, stop_id: str, now: datetime) -> list[tuple[datetime, ScheduledStopTime]]:
        results = []
        # Prior-day GTFS service can extend past midnight (times such as 24:20).
        for service_date in (now.date() - timedelta(days=1), now.date(), now.date() + timedelta(days=1)):
            for item in self.stop_times:
                if item.stop_id != stop_id or not self._service_active(item.service_id, service_date):
                    continue
                event = datetime.combine(service_date, day_time.min, self.timezone) + timedelta(
                    seconds=item.seconds_after_midnight
                )
                if event > now - timedelta(minutes=1):
                    results.append((event, item))
        results.sort(key=lambda value: value[0])
        return results

    def _read_trip_updates(self, stop_id: str) -> tuple[dict[str, datetime], set[str], bool]:
        try:
            from google.transit import gtfs_realtime_pb2
        except ImportError as exc:
            raise BusDataError(
                "GTFS-Realtime decoder missing; install dashboard requirements.txt"
            ) from exc

        url = str(self.config.get("gtfs_trip_updates_url", DEFAULT_TRIP_UPDATES_URL))
        body = _download(url, timeout=int(self.config.get("bus_request_timeout_seconds", 12)))
        feed = gtfs_realtime_pb2.FeedMessage()
        try:
            feed.ParseFromString(body)
        except Exception as exc:
            raise BusDataError(f"Could not decode Translink GTFS-Realtime feed: {exc}") from exc

        now_utc = datetime.now(timezone.utc)
        header_time = datetime.fromtimestamp(feed.header.timestamp, timezone.utc) if feed.header.timestamp else now_utc
        max_age = int(self.config.get("max_realtime_age_seconds", 180))
        feed_is_fresh = (now_utc - header_time).total_seconds() <= max_age
        predictions: dict[str, datetime] = {}
        canceled: set[str] = set()
        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue
            update = entity.trip_update
            trip = update.trip
            trip_id = trip.trip_id
            if not trip_id or trip_id not in self.trip_services:
                continue
            if trip.route_id and trip.route_id not in self.route_ids and trip.route_id != self.route_short_name:
                continue
            if trip.schedule_relationship == gtfs_realtime_pb2.TripDescriptor.CANCELED:
                canceled.add(trip_id)
                continue

            scheduled = next(
                (item for item in self.stop_times if item.trip_id == trip_id and item.stop_id == stop_id), None
            )
            for stop_update in update.stop_time_update:
                matches_stop = bool(stop_update.stop_id and stop_update.stop_id == stop_id)
                if not stop_update.stop_id and scheduled and stop_update.stop_sequence == scheduled.stop_sequence:
                    matches_stop = True
                if not matches_stop:
                    continue
                event = stop_update.arrival if stop_update.HasField("arrival") else None
                if event is None and stop_update.HasField("departure"):
                    event = stop_update.departure
                if event is None:
                    continue
                if event.time:
                    prediction = datetime.fromtimestamp(event.time, self.timezone)
                elif event.delay and scheduled:
                    base = self._scheduled_event(scheduled, trip.start_date)
                    prediction = base + timedelta(seconds=event.delay)
                else:
                    continue
                predictions[trip_id] = prediction
        if not feed_is_fresh:
            # A stale cancellation is no more trustworthy than a stale arrival estimate.
            return {}, set(), False
        return predictions, canceled, feed_is_fresh

    def _scheduled_event(self, item: ScheduledStopTime, start_date: str = "") -> datetime:
        if len(start_date) == 8:
            service_date = datetime.strptime(start_date, "%Y%m%d").date()
        else:
            service_date = datetime.now(self.timezone).date()
        return datetime.combine(service_date, day_time.min, self.timezone) + timedelta(
            seconds=item.seconds_after_midnight
        )

    def next_arrivals(self, now: datetime | None = None) -> list[BusArrival]:
        """Return the next scheduled or realtime arrival for each configured route."""
        local_now = (now or datetime.now(self.timezone)).astimezone(self.timezone)
        direction = "inbound" if local_now.hour < int(self.config.get("bus_noon_switch_hour", 12)) else "outbound"
        public_stop_code = self.stop_ids[direction]
        stop_id = self.gtfs_stop_ids.get(direction, public_stop_code)
        destinations = {
            "150": self.destinations,
            "153": {
                "inbound": str(self.config.get("bus_153_inbound_destination", "City")),
                "outbound": str(self.config.get("bus_153_outbound_destination", "Drewvale")),
            },
        }
        departures = self._scheduled_departures(stop_id, local_now)
        try:
            predictions, canceled, feed_is_fresh = self._read_trip_updates(stop_id) if departures else ({}, set(), False)
        except BusDataError as exc:
            LOG.warning("Realtime bus prediction unavailable; using timetable: %s", exc)
            predictions, canceled, feed_is_fresh = {}, set(), False

        candidates: dict[str, list[tuple[datetime, str]]] = {
            route: [] for route in self.route_short_names
        }
        for scheduled_event, item in departures:
            if item.trip_id in canceled:
                continue
            predicted = predictions.get(item.trip_id) if feed_is_fresh else None
            # Avoid applying a prediction for today's trip to the same recurring
            # GTFS trip ID on a later service day.
            if predicted is not None and abs((predicted - scheduled_event).total_seconds()) > 3 * 3600:
                predicted = None
            event_time = predicted or scheduled_event
            if event_time <= local_now - timedelta(minutes=1):
                continue
            source = "realtime" if predicted is not None else "scheduled"
            candidates[item.route].append((event_time, source))

        arrivals = []
        for route in self.route_short_names:
            route_candidates = candidates.get(route, [])
            if not route_candidates:
                continue
            arrival, source = min(route_candidates, key=lambda item: item[0])
            route_destination = destinations.get(route, self.destinations)[direction]
            arrivals.append(
                BusArrival(route, direction, route_destination, public_stop_code, arrival, source)
            )
        if arrivals:
            return arrivals

        return [
            BusArrival(
                self.route_short_name,
                direction,
                destinations.get(self.route_short_name, self.destinations)[direction],
                public_stop_code,
                None,
                "unknown",
            )
        ]

    def next_arrival(self, now: datetime | None = None) -> BusArrival:
        """Compatibility helper returning the nearest configured route arrival."""
        arrivals = self.next_arrivals(now)
        return min(
            (arrival for arrival in arrivals if arrival.arrival is not None),
            key=lambda arrival: arrival.arrival,
            default=arrivals[0],
        )


def sample_arrival(
    direction: str = "inbound", eta_minutes: int = 8, source: str = "realtime", route: str = "150"
) -> BusArrival:
    """Create predictable offline data for trying the bus renderer."""
    if direction not in {"inbound", "outbound"}:
        raise ValueError("direction must be inbound or outbound")
    now = datetime.now(TRANSIT_TZ)
    return BusArrival(
        route=route,
        direction=direction,
        destination=("Inbound" if direction == "inbound" else "Outbound"),
        stop_id="000000",
        arrival=now + timedelta(minutes=eta_minutes),
        source=source,
    )
