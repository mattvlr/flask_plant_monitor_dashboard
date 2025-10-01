# Indoor Greenhouse Flask Dashboard

A modern Tailwind + Chart.js powered dashboard to monitor your indoor greenhouse with timelapse generation.

## Features
- Tent temperature & humidity live tiles
- Two plant moisture tiles
- Live mini-sparkline charts (last 60 samples) via polling API
- Manual capture button (captures webcam frame)
- Timelapse generation button (stitches all captured frames)
- Stores sensor readings & timelapses in Postgres

## Stack
- Flask
- SQLAlchemy
- Postgres
- Tailwind CDN
- Chart.js
- OpenCV for capture & timelapse

## Environment
Set `DATABASE_URL` env var, e.g.
```
export DATABASE_URL=postgresql://user:pass@localhost:5432/plantdb
```

## Sensor Data Ingestion
If you already insert into `sensor_readings` from another process (Grafana pipeline), ensure rows follow:
```
metric: temp|humidity|moisture
plant_id: NULL for tent metrics, 1 or 2 for moisture
value: float
```

## Run
```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

## Automatic Capture (cron example)
Capture every 15 minutes:
```
*/15 * * * * /path/to/venv/bin/python -c "from app import create_app; app=create_app(); app.app_context().push(); from app.camera import capture_frame; capture_frame()" >> /tmp/capture.log 2>&1
```

## Timelapse
Build on demand via UI button or cron daily:
```
0 1 * * * /path/to/venv/bin/python -c "from app import create_app; app=create_app(); app.app_context().push(); from app.camera import build_timelapse; build_timelapse()" >> /tmp/timelapse.log 2>&1
```
