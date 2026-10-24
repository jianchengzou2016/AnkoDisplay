# AnkoDisplay

A small Windows-friendly Python dashboard for a Kmart Anko 16×16 iDotMatrix Bluetooth LED display. The dashboard renders weather and South East Queensland bus information as 16×16 PNG images and delegates Bluetooth uploads to the existing `python3-idotmatrix-client` command-line client.

The dashboard and Bluetooth client remain separate: no protocol implementation is duplicated in this project.

## Requirements

- Windows 10 or 11 with Bluetooth support
- Python 3.10 or later
- An iDotMatrix-compatible display and its Bluetooth address
- Internet access for live weather and Translink data

## Setup

From PowerShell at the repository root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\dashboard\requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .\python3-idotmatrix-client-main
Copy-Item .\dashboard\config.example.json .\dashboard\config.json
```

Edit `dashboard\config.json` and set `display_address` to the Bluetooth address of your display. Review the location, bus stop IDs and refresh intervals for your use. Keep `config.json` private; it is intentionally excluded from Git.

The install command installs the iDotMatrix client and its declared dependencies in the same environment. Its own setup details are in [`python3-idotmatrix-client-main/README.md`](python3-idotmatrix-client-main/README.md). Keep `client_directory` pointed at the bundled client folder and `client_python` pointed at the environment's Python executable.

## Run

Render offline sample weather images without network or Bluetooth:

```powershell
.\.venv\Scripts\python.exe .\dashboard\app.py --sample sunny
```

Preview a single live update without uploading:

```powershell
.\.venv\Scripts\python.exe .\dashboard\app.py --once
```

Run continuously and upload changed pages:

```powershell
.\.venv\Scripts\python.exe .\dashboard\app.py --upload
```

See [`dashboard/README.md`](dashboard/README.md) for weather, bus selection, configuration options, data sources and attribution.

## Versioning and releases

Release versions use Git tags prefixed with `v` (for example, `v0.1.0`). The tag is the version source for future packaging/release automation. The current project is a source POC; a Windows installer has not yet been added.

## Third-party software and data

The bundled iDotMatrix client is distributed under its included GPL-3.0 license. Its license and notices are retained in its directory. Weather uses Open-Meteo, and bus data uses Translink Open Data; see the dashboard README for service terms and attribution.
