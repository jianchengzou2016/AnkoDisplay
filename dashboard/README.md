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

Configure the public stop codes, destination labels, route numbers, timezone and noon switch in `config.json`. The app resolves public stop codes through GTFS `stops.txt` when they differ from internal GTFS stop IDs. The GTFS service calendar and stop times determine which configured trips run at each time. A fresh realtime stop prediction is used where available; otherwise the timetable time is shown. If realtime cannot be read, the error is logged and the timetable remains usable. The page shows the route number, a destination marker, an ETA in minutes, and a colored status pixel: green for realtime, amber for timetable, red when no arrival is available.

When both routes have an arrival within `bus_alternate_window_minutes` (60 minutes by default), the display alternates route pages at each one-minute bus refresh. That gives each route a page about every two minutes. If both are not within that window, it shows the nearest upcoming route without alternating. Change the window in `config.json` if you want “coming” to mean a different period.

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

## Weather data

Forecast and location lookup use the [Open-Meteo Forecast API](https://open-meteo.com/en/docs) and [Geocoding API](https://open-meteo.com/en/docs/geocoding-api). The free endpoints require no key for non-commercial use. Review Open-Meteo's [usage terms](https://open-meteo.com/en/terms). The current “umbrella likely” starter rule uses rain/drizzle forecast codes or a precipitation probability of at least 50% over the next two hours. Both values can be changed in `config.json`.

## Transit data and attribution

Static South East Queensland GTFS timetables and the bus GTFS-Realtime TripUpdates feed are supplied by [Translink Open Data](https://translink.com.au/about-translink/open-data). The official feed documentation identifies GTFS-Realtime v2; the public endpoint used here does not require an API key. Data is provided under [Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/). Keep this attribution with the project and follow Translink's [open-data terms](https://translink.com.au/about-translink/open-data/terms-and-conditions), including its requirement to provide and keep contact details current when using the data. This POC looks up the route numbers configured in `config.json` at the two configured stops.
