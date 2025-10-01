# Indoor Greenhouse Dashboard

A Flask-based monitoring dashboard for a small indoor greenhouse. It polls sensor readings from a SQLite database, proxies image captures from an ESP32-style camera, and can stitch captured frames into MP4/WEBM timelapses. The UI is built with Tailwind CSS and Chart.js for quick visuals of temperature, humidity, light, and moisture trends.

## Features
- Real-time dashboard tiles with the latest tent and plant metrics.
- Mini sparklines that refresh every few seconds from `/api/latest`.
- Manual snapshot trigger and scheduled background capture thread.
- Timelapse builder (ffmpeg preferred, OpenCV fallback) with download links.
- Sun position calculations, light schedule controls, and database statistics.
- Outlier detection helpers to prune noisy sensor readings.

## Repository layout
```
├── app/
│   ├── __init__.py        # Flask application factory
│   ├── camera.py          # Capture/timelapse helpers
│   ├── db.py              # SQLite helpers and CLI command
│   ├── routes.py          # Blueprint with UI + API endpoints
│   ├── static/            # Tailwind/Chart.js assets
│   └── templates/         # Jinja templates
├── config.py              # Default configuration values
├── requirements.txt       # Python dependencies
└── run.py                 # Local entry-point (port 5001)
```

## Prerequisites
- Python 3.11+ (tested on 3.12).
- `ffmpeg` on the `PATH` (recommended for timelapses; OpenCV fallback is slower/larger).
- An HTTP-accessible snapshot endpoint (e.g., ESP32 camera) for manual/automatic captures.

## Quick start
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```
This starts the development server at http://127.0.0.1:5001. The first launch will create `instance/plant_monitor.db` if it does not exist and ensure capture/timelapse folders are present.

### Initial database setup
You can also (re)initialise the schema manually via the Flask CLI:
```bash
flask --app run.py init-db
```
The schema lives in `app/schema.sql` and contains a single `readings` table that stores `sensor_type`, `plant_id`, `reading`, and timestamps.

### Feeding sensor data
Insert readings from external scripts or microcontrollers with a simple POST:
```bash
curl -X POST http://127.0.0.1:5001/add_data \
     -H 'Content-Type: application/json' \
     -d '{"sensor_type": "bme280_temp_f", "reading": 74.3}'
```
Supported naming conventions:
- Tent-wide metrics (temperature, humidity, light, etc.) use `plant_id = null`.
- Plant-specific probes use `plant_id = 1`, `2`, etc. and appear as `moisture_plant1`, `moisture_plant2` in the UI.

## Configuration
All settings fall back to sensible defaults in `config.py` but can be overridden with environment variables before launching the app.

| Environment variable | Default | Description |
| --- | --- | --- |
| `SECRET_KEY` | `you-will-never-guess` | Flask session secret. |
| `CAPTURE_INTERVAL_SECONDS` | `900` | Background capture cadence (seconds). Minimum enforced at 60s. |
| `SNAPSHOT_URL` | `http://192.168.50.101:8000/snapshot` | Remote camera snapshot endpoint used by `/capture`. |
| `TIMELAPSE_FOLDER` / `CAPTURE_FOLDER` | under `app/` | Where images and timelapses are stored. |
| `LIGHT_ON_HOUR` / `LIGHT_OFF_HOUR` | `19` / `9` | Hour (0–23) for grow lights schedule. Minutes available via `LIGHT_ON_MINUTE`, `LIGHT_OFF_MINUTE`. |
| `LOCATION_LAT` / `LOCATION_LON` | `34.749571` / `-92.356872` | Used for sun position API. |
| `CAMERA_BASE_URL` | `http://192.168.50.59` | Base address for future camera integrations. |

> **Note:** The default database is SQLite. If you plan to run against Postgres or another backend, adapt `app/db.py` and `Config.DATABASE` accordingly.

## Media folders
- `app/capture/` — Raw JPEG snapshots organised by date.
- `app/timelapse/` — Rendered timelapse videos (`.mp4` and `.webm`).

These directories are created automatically and ignored by Git. Use the **Settings** page to prune captures, delete timelapses, or adjust the capture interval without restarts.

## Running tests
The project currently has no automated tests, but the suite is ready for pytest:
```bash
pytest
```
Consider adding tests for the API endpoints, database helpers, and camera utilities as the project grows.

## Deployment tips
- Use a production-ready server such as Gunicorn: `gunicorn --bind 0.0.0.0:8000 'app:create_app()'`.
- Configure systemd/cron to call `capture_frame` and `build_timelapse` at your desired cadence if you do not want the built-in background thread.
- Backup the SQLite database (`instance/plant_monitor.db`) and media directories regularly.
