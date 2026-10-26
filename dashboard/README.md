# Anko 16x16 dashboard POC

A Windows-friendly Python dashboard for the Kmart Anko iDotMatrix display. It combines a short-term weather symbol with configured bus route arrivals. The dashboard creates exact 16x16 RGB PNGs and calls the existing iDotMatrix client's known-working image command to upload them. It does not contain or change Bluetooth protocol code.

## Install

From the repository root in PowerShell (see the root README for the full setup):

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\dashboard\requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .\python3-idotmatrix-client-main
Copy-Item .\dashboard\config.example.json .\dashboard\config.json
```

This installs Pillow for the images, `gtfs-realtime-bindings` to decode Translink's GTFS-Realtime protobuf feed, and `tzdata` because Windows Python does not always ship the IANA time-zone database used for the Brisbane noon switch. The static timetable ZIP is downloaded on first run and cached at `dashboard\cache\SEQ_GTFS.zip` for 14 days. A network connection is needed to obtain the timetable and live predictions. No API key is required.

## Offline image previews

```powershell
$py = ".\python3-idotmatrix-client-main\venv_windows\Scripts\python.exe"
& $py .\dashboard\app.py --sample sunny
& $py .\dashboard\app.py --sample cloudy
& $py .\dashboard\app.py --sample rain
& $py .\dashboard\app.py --sample thunderstorm
& $py .\dashboard\app.py --sample-bus
```

Weather sample mode and `--sample-bus` do not use the Internet or Bluetooth. PNGs are saved under `dashboard\output\`.

## Bus selection and display rotation

Configure the public stop codes, destination labels, route numbers, timezone and noon switch in `config.json`. The app resolves public stop codes through GTFS `stops.txt` when they differ from internal GTFS stop IDs. The GTFS service calendar and stop times determine which configured trips run at each time. Fresh realtime predictions at the exact configured stop can be used by passenger route number even when Translink's internal trip or route IDs have changed since the cached timetable. Configure recognized internal route-ID prefixes in `realtime_route_id_prefixes` (for example, `"150": ["150-"]`); update these values if Translink changes its route-ID format. Absolute API event times are used directly; delay-only events require a unique matching static departure by route and trip start time. If a match cannot be made, or realtime is stale or unavailable, the timetable remains the fallback. Set `log_level` to `DEBUG` to see detailed feed coverage and per-trip selection diagnostics; the default `INFO` level keeps routine output concise while retaining arrival and upload summaries. The page shows the route number, a destination marker, an ETA in minutes, and a colored status pixel: green for realtime, amber for timetable, red when no arrival is available.

When both routes have an arrival within `bus_alternate_window_minutes` (60 minutes by default), the display alternates route pages at each one-minute bus refresh. That gives each route a page about every two minutes. If both are not within that window, it shows the nearest upcoming route without alternating. Each route page shows its next arrival and, when the following arrival is within `bus_second_arrival_window_minutes` (20 minutes by default), the following ETA too. For example, `5/18` means the next bus is in 5 minutes and the following bus in 18 minutes. When there is only one ETA to show, the display keeps the `5m` format. Change either window in `config.json` to adjust what counts as “coming.”

Bus data refreshes every 60 seconds. Weather refreshes every 300 seconds. The bus page is shown by default; weather takes a brief 10-second turn once per five-minute display cycle. These intervals, the alternation window, noon switch, stop IDs, display address, and paths are configurable in `config.json`. The display's built-in countdown mode is not used; the dashboard redraws a static ETA image each time the minute count changes.

## Run and upload

Preview one live update without uploading:

```powershell
$py = ".\python3-idotmatrix-client-main\venv_windows\Scripts\python.exe"
& $py .\dashboard\app.py --once
```

Upload updates continuously:

```powershell
$py = ".\python3-idotmatrix-client-main\venv_windows\Scripts\python.exe"
& $py .\dashboard\app.py --upload
```

The app uploads only when the visible page changes. Press Ctrl+C to stop. Keep the Anko phone app disconnected while the PC is controlling the display. The existing iDotMatrix client and its command-line interface are configured with `client_directory`, `client_python`, and `display_address` in `config.json`.

## Active hours POC

Active-hour control is opt-in. In `config.json`, set `active_hours.enabled` to `true` and configure one or more local-time windows:

```json
"active_hours": {
  "enabled": true,
  "windows": [
    { "start": "06:30", "end": "08:30", "days": ["mon", "tue", "wed", "thu", "fri"] },
    { "start": "15:00", "end": "22:00" }
  ]
}
```

Times use the Windows PC's local time and 24-hour `HH:MM` format. `days` is optional and defaults to every day; supported values are `mon` through `sun`. Overnight windows are supported. Start is inclusive and end is exclusive. Run the normal `--upload` command: outside a window it waits without uploading; at the start it sends screen-on before any image; at the end it sends screen-off and exits. Configure Task Scheduler to start the app before a window if the PC will be asleep; the app waits until the window actually starts before waking the display. `--once` exits immediately if started outside active hours.

For a manual Windows Task Scheduler POC, create one task for each active-window start, scheduled two minutes early, with **Wake the computer to run this task** enabled and an action that starts the configured Python executable with `dashboard\app.py --upload` as arguments (set the repository root as the working directory). Windows power settings, firmware and the Bluetooth adapter must support wake/resume for this to work. The dashboard does not create Windows tasks or put the PC to sleep. At each window end it turns off the display and exits; you can add a separate delayed standby task after confirming the Wyse's sleep/wake behavior manually. Keep such a task disabled until wake and Bluetooth reconnect have been verified.

## Weather data

Forecast and location lookup use the [Open-Meteo Forecast API](https://open-meteo.com/en/docs) and [Geocoding API](https://open-meteo.com/en/docs/geocoding-api). The free endpoints require no key for non-commercial use. Review Open-Meteo's [usage terms](https://open-meteo.com/en/terms). The current “umbrella likely” starter rule uses rain/drizzle forecast codes or a precipitation probability of at least 50% over the next two hours. Both values can be changed in `config.json`.

## Transit data and attribution

Static South East Queensland GTFS timetables and the bus GTFS-Realtime TripUpdates feed are supplied by [Translink Open Data](https://translink.com.au/about-translink/open-data). The official feed documentation identifies GTFS-Realtime v2; the public endpoint used here does not require an API key. Data is provided under [Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/). Keep this attribution with the project and follow Translink's [open-data terms](https://translink.com.au/about-translink/open-data/terms-and-conditions), including its requirement to provide and keep contact details current when using the data. This POC looks up the route numbers configured in `config.json` at the two configured stops.
