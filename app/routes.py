from flask import Blueprint, render_template, jsonify, send_from_directory, current_app, request, redirect, url_for, abort
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from astral.sun import sun, elevation, azimuth
from astral import LocationInfo
from .camera import capture_frame, build_timelapse, get_latest_image
from .db import get_db, init_db
import shutil  # added
import requests  # for camera proxy
from statistics import median, mean, stdev, StatisticsError  # added

bp = Blueprint('main', __name__)

def get_conn():
    return get_db()

@bp.route('/')
def index():
    latest_timelapse = None  # not stored in DB now; just pick last built file
    tl_folder = current_app.config['TIMELAPSE_FOLDER']
    tl_files = [f for f in os.listdir(tl_folder) if f.endswith('.mp4')]
    if tl_files:
        latest_timelapse = {'filename': max(tl_files, key=lambda f: os.path.getmtime(os.path.join(tl_folder, f)))}
    latest_image = get_latest_image()
    light_on_hour = current_app.config.get('LIGHT_ON_HOUR', 20)  # Default to 8 PM
    # Added: Light off hour (default 11 AM)
    light_off_hour = current_app.config.get('LIGHT_OFF_HOUR', 11)
    # New: minutes
    light_on_minute = current_app.config.get('LIGHT_ON_MINUTE', 0)
    light_off_minute = current_app.config.get('LIGHT_OFF_MINUTE', 0)
    return render_template('dashboard.html', latest_timelapse=latest_timelapse, latest_image=latest_image,
                           light_on_hour=light_on_hour, light_off_hour=light_off_hour,
                           light_on_minute=light_on_minute, light_off_minute=light_off_minute)

@bp.route('/api/latest')
def api_latest():
    db = get_conn()
    query = """
        SELECT sensor_type, plant_id, reading
        FROM readings
        WHERE id IN (
            SELECT MAX(id)
            FROM readings
            GROUP BY sensor_type, plant_id
        );
    """
    rows = db.execute(query).fetchall()
    data = {}

    # Map to UI keys
    sensor_map = {
        'bh1750_lux': 'light',
        'bme280_humidity_pct': 'humidity',
        'dht11_humidity_pct': 'humidity',  # fallback if present
        'bme280_pressure_hpa': 'pressure',
        'bmp280_pressure_hpa': 'pressure',
        'bme280_altitude_m': 'altitude',
        'bmp280_altitude_m': 'altitude',
        'moisture': 'moisture'
    }
    tent_sensors = ['light', 'temp', 'humidity', 'pressure', 'altitude']

    def to_f_if_c(stype: str, val: float):
        if stype.endswith('_temp_c'):
            try:
                return val * 9.0/5.0 + 32.0
            except Exception:
                return val
        return val

    # Prefer BME/BMP over DHT11 for temperature
    temp_rank_map = {
        'bme280_temp_f': 3, 'bme280_temp_c': 3,
        'bmp280_temp_f': 2, 'bmp280_temp_c': 2,
        'dht11_temp_f': 1
    }
    best_temp = None
    best_temp_rank = -1




    for row in rows:
        stype = row['sensor_type']
        val = float(row['reading'])
            #if any temp is > 150F ignore it
        if stype in temp_rank_map and val > 150.0:
            continue
        # Temperature handling with precedence
        if stype in temp_rank_map:
            rank = temp_rank_map[stype]
            if rank > best_temp_rank:
                best_temp = to_f_if_c(stype, val)
                best_temp_rank = rank
            continue  # don't process via generic path
        key = sensor_map.get(stype)
        if not key:
            continue
        if row['plant_id'] and key not in tent_sensors:
            key = f"{key}_plant{row['plant_id']}"
        data[key] = val

    if best_temp is not None:
        data['temp'] = best_temp

    return jsonify(data)

@bp.route('/add_data', methods=['POST'])
def add_data():
    if not request.json:
        return jsonify({'error': 'Invalid JSON'}), 400
    
    sensor_type = request.json.get('sensor_type')
    reading = request.json.get('reading')
    plant_id = request.json.get('plant_id')

    if not sensor_type or reading is None:
        return jsonify({'error': 'Missing sensor_type or reading'}), 400

    try:
        reading = float(reading)
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid reading format'}), 400

    db = get_conn()
    db.execute(
        'INSERT INTO readings (sensor_type, plant_id, reading) VALUES (?, ?, ?)',
        (sensor_type, plant_id, reading)
    )
    db.commit()
    return jsonify({'success': True}), 201

@bp.route('/capture', methods=['POST'])
def manual_capture():
    path = capture_frame()
    return jsonify({'captured': os.path.basename(path)})

@bp.route('/timelapse', methods=['POST'])
def make_timelapse():
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    filename = f'timelapse_{ts}.mp4'
    path = build_timelapse(output_name=filename)
    return jsonify({'timelapse': os.path.basename(path)})

@bp.route('/timelapse/<filename>')
def serve_timelapse(filename):
    return send_from_directory(current_app.config['TIMELAPSE_FOLDER'], filename)

@bp.route('/latest-image')
def latest_image():
    img = get_latest_image()
    if not img:
        return jsonify({'image': None})
    return jsonify({'image': img})

@bp.route('/capture/<path:filename>')
def serve_capture(filename):
    return send_from_directory(current_app.config['CAPTURE_FOLDER'], filename)

@bp.route('/api/sun_position')
def sun_position():
    lat = current_app.config['LOCATION_LAT']
    lon = current_app.config['LOCATION_LON']
    tz_name = getattr(current_app.config, 'LOCATION_TZ', 'America/Chicago')
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo('UTC')
        tz_name = 'UTC'
    loc = LocationInfo(name='Site', region='Local', timezone=tz_name, latitude=lat, longitude=lon)
    now = datetime.now(tz)
    try:
        s = sun(loc.observer, date=now.date(), tzinfo=tz)
    except Exception as e:
        return jsonify({'error': 'sun calc failed', 'detail': str(e)}), 500
    start_of_day = datetime(now.year, now.month, now.day, tzinfo=tz)
    seconds_in_day = 86400
    seconds_since_midnight = (now - start_of_day).total_seconds()
    day_progress = seconds_since_midnight / seconds_in_day  # 0 @ midnight, 0.5 noon, 1 next midnight

    observer = loc.observer
    try:
        elev = elevation(observer, now)
        az = azimuth(observer, now)
    except Exception:
        elev = None
        az = None

    # Daylight only progress (None at night)
    if s['sunrise'] <= now <= s['sunset']:
        daylight_progress = (now - s['sunrise']).total_seconds() / (s['sunset'] - s['sunrise']).total_seconds()
    else:
        daylight_progress = None

    return jsonify({
        'sunrise': s['sunrise'].isoformat(),
        'sunset': s['sunset'].isoformat(),
        'solar_noon': s['noon'].isoformat(),
        'now': now.isoformat(),
        'sun_progress': day_progress,
        'daylight_progress': daylight_progress,
        'elevation_deg': elev,
        'azimuth_deg': az,
        'is_day': s['sunrise'] <= now <= s['sunset'],
        'timezone': tz_name
    })

# Settings page
@bp.route('/settings', methods=['GET', 'POST'])
def settings():
    tl_folder = current_app.config['TIMELAPSE_FOLDER']
    timelapses = sorted([f for f in os.listdir(tl_folder) if f.endswith('.mp4') or f.endswith('.webm')])
    capture_interval = current_app.config.get('CAPTURE_INTERVAL_SECONDS', 900)
    light_on_hour = current_app.config.get('LIGHT_ON_HOUR', 20) # Default 8 PM
    # Added: light off hour default 11 AM
    light_off_hour = current_app.config.get('LIGHT_OFF_HOUR', 11)
    # New: minute precision
    light_on_minute = current_app.config.get('LIGHT_ON_MINUTE', 0)
    light_off_minute = current_app.config.get('LIGHT_OFF_MINUTE', 0)
    
    db = get_db()
    latest_readings = db.execute('SELECT * FROM readings ORDER BY time_inserted DESC LIMIT 5').fetchall()

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'update_light_on_hour':
            # Support HH or HH:MM
            raw = request.form.get('light_on_time') or request.form.get('light_on_hour')
            try:
                if raw and ':' in str(raw):
                    h_str, m_str = str(raw).split(':', 1)
                    h = int(h_str)
                    m = int(m_str)
                else:
                    h = int(raw)
                    m = int(request.form.get('light_on_minute', light_on_minute))
                current_app.config['LIGHT_ON_HOUR'] = min(23, max(0, h))
                current_app.config['LIGHT_ON_MINUTE'] = min(59, max(0, m))
            except (ValueError, TypeError):
                pass
            return redirect(url_for('main.settings'))
        # Added: handle updating lights off hour
        if action == 'update_light_off_hour':
            # Support HH or HH:MM
            raw = request.form.get('light_off_time') or request.form.get('light_off_hour')
            try:
                if raw and ':' in str(raw):
                    h_str, m_str = str(raw).split(':', 1)
                    h = int(h_str)
                    m = int(m_str)
                else:
                    h = int(raw)
                    m = int(request.form.get('light_off_minute', light_off_minute))
                current_app.config['LIGHT_OFF_HOUR'] = min(23, max(0, h))
                current_app.config['LIGHT_OFF_MINUTE'] = min(59, max(0, m))
            except (ValueError, TypeError):
                pass
            return redirect(url_for('main.settings'))
        if action == 'delete' and (fname := request.form.get('filename')):
            target = os.path.join(tl_folder, fname)
            if os.path.isfile(target):
                try:
                    os.remove(target)
                    # remove paired webm/mp4 if exists
                    base, ext = os.path.splitext(target)
                    other = base + ('.webm' if ext == '.mp4' else '.mp4')
                    if os.path.exists(other):
                        os.remove(other)
                except Exception as e:
                    current_app.logger.error(f"Delete failed: {e}")
            return redirect(url_for('main.settings'))
        if action == 'update_interval':
            try:
                new_interval = int(request.form.get('interval', capture_interval))
                current_app.config['CAPTURE_INTERVAL_SECONDS'] = max(60, new_interval)
            except ValueError:
                pass
            return redirect(url_for('main.settings'))
        if action == 'prune_captures':
            days = int(request.form.get('days', '14'))
            cutoff = datetime.utcnow().timestamp() - days*86400
            root = current_app.config['CAPTURE_FOLDER']
            removed = 0
            for day in os.listdir(root):
                day_path = os.path.join(root, day)
                if os.path.isdir(day_path):
                    try:
                        ts = datetime.strptime(day, '%Y-%m-%d').timestamp()
                        if ts < cutoff:
                            # remove folder
                            for f in os.listdir(day_path):
                                os.remove(os.path.join(day_path, f))
                            os.rmdir(day_path)
                            removed += 1
                    except Exception:
                        continue
            return redirect(url_for('main.settings'))
        if action == 'db_create':
            try:
                init_db()
            except Exception as e:
                current_app.logger.error(f"DB init failed: {e}")
            return redirect(url_for('main.settings'))
        if action == 'db_prune':
            days = int(request.form.get('days', '30'))
            cutoff = datetime.utcnow() - timedelta(days=days)
            try:
                db = get_db()
                db.execute('DELETE FROM readings WHERE time_inserted < ?', (cutoff,))
                db.commit()
            except Exception as e:
                current_app.logger.error(f"DB prune failed: {e}")
            return redirect(url_for('main.settings'))
        # New: delete single reading (outlier row)
        if action == 'delete_reading' and (rid := request.form.get('reading_id')):
            try:
                db.execute('DELETE FROM readings WHERE id = ?', (int(rid),))
                db.commit()
            except Exception as e:
                current_app.logger.error(f"Delete reading failed: {e}")
            return redirect(url_for('main.settings'))
        # New: delete multiple selected outliers
        if action == 'delete_outliers_selected':
            ids = request.form.getlist('reading_ids')
            if ids:
                try:
                    placeholders = ','.join('?' for _ in ids)
                    db.execute(f'DELETE FROM readings WHERE id IN ({placeholders})', tuple(int(i) for i in ids))
                    db.commit()
                except Exception as e:
                    current_app.logger.error(f"Bulk delete outliers failed: {e}")
            return redirect(url_for('main.settings'))

    # ---- Database statistics (for settings page) ----
    db_stats = {}
    sensor_stats = []
    try:
        row = db.execute('SELECT COUNT(*) FROM readings').fetchone()
        total_rows = row[0] if row else 0
        db_stats['total_rows'] = total_rows

        if total_rows > 0:
            oldest_newest = db.execute('SELECT MIN(time_inserted), MAX(time_inserted) FROM readings').fetchone()
            raw_oldest, raw_newest = oldest_newest[0], oldest_newest[1]

            def _parse_ts(v):
                from datetime import datetime
                if v is None:
                    return None
                if hasattr(v, 'isoformat') and not isinstance(v, str):  # already datetime-like
                    return v
                if isinstance(v, str):
                    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f'):  # common SQLite formats
                        try:
                            return datetime.strptime(v, fmt)
                        except ValueError:
                            continue
                    try:
                        return datetime.fromisoformat(v)
                    except Exception:
                        return None
                return None

            oldest_dt = _parse_ts(raw_oldest)
            newest_dt = _parse_ts(raw_newest)
            # Store display values as original strings or ISO (fallback)
            db_stats['oldest'] = raw_oldest if isinstance(raw_oldest, str) else (oldest_dt.isoformat() if oldest_dt else None)
            db_stats['newest'] = raw_newest if isinstance(raw_newest, str) else (newest_dt.isoformat() if newest_dt else None)
            if oldest_dt and newest_dt:
                span_seconds = (newest_dt - oldest_dt).total_seconds() or 1
                span_days = span_seconds / 86400.0
            else:
                span_days = 0
            db_stats['span_days'] = span_days
            db_stats['rows_per_day'] = total_rows / span_days if span_days > 0 else total_rows
        else:
            db_stats['oldest'] = None
            db_stats['newest'] = None
            db_stats['span_days'] = 0
            db_stats['rows_per_day'] = 0

        # Recent activity counts
        db_stats['rows_24h'] = db.execute("SELECT COUNT(*) FROM readings WHERE time_inserted >= datetime('now','-1 day')").fetchone()[0]
        db_stats['rows_7d'] = db.execute("SELECT COUNT(*) FROM readings WHERE time_inserted >= datetime('now','-7 day')").fetchone()[0]
        db_stats['rows_30d'] = db.execute("SELECT COUNT(*) FROM readings WHERE time_inserted >= datetime('now','-30 day')").fetchone()[0]

        # Distincts / plant breakdown
        db_stats['distinct_sensors'] = db.execute('SELECT COUNT(DISTINCT sensor_type) FROM readings').fetchone()[0]
        db_stats['distinct_plants'] = db.execute('SELECT COUNT(DISTINCT plant_id) FROM readings WHERE plant_id IS NOT NULL').fetchone()[0]
        db_stats['rows_null_plant'] = db.execute('SELECT COUNT(*) FROM readings WHERE plant_id IS NULL').fetchone()[0]
        db_stats['rows_with_plant'] = db.execute('SELECT COUNT(*) FROM readings WHERE plant_id IS NOT NULL').fetchone()[0]

        # Per-sensor aggregated stats
        sensor_rows = db.execute('''
            SELECT sensor_type,
                   COUNT(*) AS cnt,
                   MIN(reading) AS min_reading,
                   MAX(reading) AS max_reading,
                   AVG(reading) AS avg_reading,
                   MIN(time_inserted) AS first_ts,
                   MAX(time_inserted) AS last_ts
            FROM readings
            GROUP BY sensor_type
            ORDER BY sensor_type
        ''').fetchall()
        for r in sensor_rows:
            sensor_stats.append({
                'sensor_type': r['sensor_type'],
                'count': r['cnt'],
                'min': r['min_reading'],
                'max': r['max_reading'],
                'avg': r['avg_reading'],
                'first_ts': r['first_ts'],
                'last_ts': r['last_ts']
            })

        # Database file size
        db_path = current_app.config['DATABASE']
        try:
            size_bytes = os.path.getsize(db_path)
        except OSError:
            size_bytes = 0
        db_stats['db_file_size_bytes'] = size_bytes
    except Exception as e:
        current_app.logger.error(f"DB stats failed: {e}")

    # Ensure all expected keys exist to avoid template UndefinedError
    expected_defaults = {
        'total_rows': 0,
        'rows_per_day': 0,
        'span_days': 0,
        'db_file_size_bytes': 0,
        'rows_24h': 0,
        'rows_7d': 0,
        'rows_30d': 0,
        'distinct_sensors': 0,
        'distinct_plants': 0,
        'rows_with_plant': 0,
        'rows_null_plant': 0,
        'oldest': None,
        'newest': None,
    }
    for k, v in expected_defaults.items():
        db_stats.setdefault(k, v)

    # ---- Outlier detection (robust per sensor_type/plant_id) ----
    try:
        days = int(request.args.get('outlier_days', '30'))
    except ValueError:
        days = 30
    cutoff_dt = datetime.utcnow() - timedelta(days=days)
    rows = db.execute(
        'SELECT id, sensor_type, plant_id, reading, time_inserted FROM readings WHERE time_inserted >= ? ORDER BY time_inserted ASC',
        (cutoff_dt,)
    ).fetchall()

    # Group readings
    from collections import defaultdict
    groups = defaultdict(list)
    for r in rows:
        key = (r['sensor_type'], r['plant_id'])
        groups[key].append({'id': r['id'], 'reading': float(r['reading']), 'ts': r['time_inserted']})

    outliers = []
    MAD_EPS = 1e-9
    MAD_SCALE = 0.6745
    MAD_THRESHOLD = float(request.args.get('outlier_mad_thresh', '6'))  # common 6 or 7
    Z_THRESHOLD = float(request.args.get('outlier_z_thresh', '4'))

    for (stype, pid), items in groups.items():
        vals = [it['reading'] for it in items]
        n = len(vals)
        if n < 8:
            continue  # not enough data to judge
        med = median(vals)
        abs_dev = [abs(v - med) for v in vals]
        mad = median(abs_dev)
        if mad > MAD_EPS:
            # Modified Z via MAD
            for it in items:
                mz = (MAD_SCALE * (it['reading'] - med)) / mad
                score = abs(mz)
                if score >= MAD_THRESHOLD:
                    outliers.append({
                        'id': it['id'], 'sensor_type': stype, 'plant_id': pid,
                        'reading': it['reading'], 'time_inserted': it['ts'],
                        'score': score, 'method': 'MAD', 'group_n': n, 'median': med, 'mad': mad
                    })
        else:
            # Fallback Z-score if variance exists
            try:
                mu = mean(vals)
                sd = stdev(vals)
            except StatisticsError:
                sd = 0
            if sd > 0:
                for it in items:
                    z = (it['reading'] - mu) / sd
                    score = abs(z)
                    if score >= Z_THRESHOLD:
                        outliers.append({
                            'id': it['id'], 'sensor_type': stype, 'plant_id': pid,
                            'reading': it['reading'], 'time_inserted': it['ts'],
                            'score': score, 'method': 'Z', 'group_n': n, 'mean': mu, 'stdev': sd
                        })
            # else: all same values; skip

    # Sort and limit to reasonable amount for UI
    outliers.sort(key=lambda x: x['score'], reverse=True)
    OUTLIER_LIMIT = 200
    outliers = outliers[:OUTLIER_LIMIT]

    return render_template(
        'settings.html',
        timelapses=timelapses,
        capture_interval=capture_interval,
        light_on_hour=light_on_hour,
        # Added: pass light off hour to template
        light_off_hour=light_off_hour,
        # New: minutes
        light_on_minute=light_on_minute,
        light_off_minute=light_off_minute,
        TIMELAPSE_FOLDER=tl_folder,
        latest_readings=latest_readings,
        db_stats=db_stats,
        sensor_stats=sensor_stats,
        outliers=outliers,
        outlier_days=days,
        outlier_mad_thresh=MAD_THRESHOLD,
        outlier_z_thresh=Z_THRESHOLD
    )

@bp.route('/api/history')
def api_history():
    range_arg = request.args.get('range', '24h')
    aggregate_daily = False

    # Added shorter hour ranges
    if range_arg == '1h':
        delta = timedelta(hours=1)
    elif range_arg == '6h':
        delta = timedelta(hours=6)
    elif range_arg == '12h':
        delta = timedelta(hours=12)
    elif range_arg == '24h':
        delta = timedelta(hours=24)
    elif range_arg == '48h':
        delta = timedelta(hours=48)
    elif range_arg == '7d':
        delta = timedelta(days=7)
    elif range_arg == '30d':
        delta = timedelta(days=30)
    elif range_arg == '90d':
        delta = timedelta(days=90)
    elif range_arg == 'monthly':  # last 30 days aggregated by day
        delta = timedelta(days=30)
        aggregate_daily = True
    else:  # fallback default 24h
        delta = timedelta(hours=24)

    cutoff = datetime.utcnow() - delta

    db = get_conn()
    query = "SELECT time_inserted, sensor_type, plant_id, reading FROM readings WHERE time_inserted >= ? ORDER BY time_inserted ASC"
    rows = db.execute(query, (cutoff,)).fetchall()

    sensor_map = {
        'bh1750_lux': 'light',
        # temp (prefer only bme/bmp; ignore dht11 temps)
        'bme280_temp_f': 'temp',
        'bmp280_temp_f': 'temp',
        'bme280_temp_c': 'temp_c',
        'bmp280_temp_c': 'temp_c',
        # humidity (support both)
        'dht11_humidity_pct': 'humidity',
        'bme280_humidity_pct': 'humidity',
        # pressure/altitude
        'bme280_pressure_hpa': 'pressure',
        'bmp280_pressure_hpa': 'pressure',
        'bme280_altitude_m': 'altitude',
        'bmp280_altitude_m': 'altitude',
        # others
        'moisture': 'moisture'
    }
    tent_sensors = ['light', 'temp', 'humidity', 'pressure', 'altitude']

    central = ZoneInfo('America/Chicago')
    utc = ZoneInfo('UTC')

    def to_f_if_c(stype: str, val: float):
        if stype.endswith('_temp_c'):
            try:
                return val * 9.0/5.0 + 32.0
            except Exception:
                return val
        return val

    if aggregate_daily:
        from collections import defaultdict
        daily = defaultdict(lambda: defaultdict(list))
        for row in rows:
            stype = row['sensor_type']
            # Skip legacy DHT11 temp series
            if stype.startswith('dht11_temp'):
                continue
            sensor_name = sensor_map.get(stype)
            if not sensor_name:
                continue
            key = sensor_name
            val = row['reading']
            if key in ('temp', 'temp_c'):
                val = to_f_if_c(stype, float(val))
                key = 'temp'
            if row['plant_id'] and key not in tent_sensors:
                key = f"{key}_plant{row['plant_id']}"
            ts = row['time_inserted']
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=utc)
            local_dt = ts.astimezone(central)
            day = local_dt.date()
            daily[key][day].append(val)
        data = {}
        for key, per_day in daily.items():
            series = []
            for day, readings in sorted(per_day.items()):
                day_dt = datetime(day.year, day.month, day.day, tzinfo=central)
                series.append({'t': int(day_dt.timestamp()*1000), 'v': float(sum(readings)/len(readings))})
            data[key] = series
    else:
        data = {}
        for row in rows:
            stype = row['sensor_type']
            if stype.startswith('dht11_temp'):
                continue
            sensor_name = sensor_map.get(stype)
            if not sensor_name:
                continue
            key = sensor_name
            val = row['reading']
            if key in ('temp', 'temp_c'):
                val = to_f_if_c(stype, float(val))
                key = 'temp'
            if row['plant_id'] and key not in tent_sensors:
                key = f"{key}_plant{row['plant_id']}"
            ts = row['time_inserted']
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=utc)
            local_dt = ts.astimezone(central)
            if key not in data:
                data[key] = []
            data[key].append({'t': int(local_dt.timestamp()*1000), 'v': float(val)})

    return jsonify({'data': data, 'tz': 'America/Chicago'})

@bp.route('/docs/')
@bp.route('/docs/<page>')
def docs(page='index'):
    pages = {
        'index': 'Overview',
        'temperature': 'Temperature / Humidity Sensor',
        # 'humidity': 'Humidity Sensor',
        'light': 'Light Sensor',
        'moisture': 'Soil Moisture Sensor',
        'tent': 'Indoor Pepper Tent (Arkansas)',
        'vpd': 'Vapor Pressure Deficit (VPD)',
        'camera': 'Camera Module'
    }
    if page not in pages:
        abort(404)
    template = f'docs/{page}.html'
    return render_template(template, pages=pages, current_page=page)

@bp.route('/api/daily_stats')
def api_daily_stats():
    """Return min/max/avg for tent-level sensors over the past 24h."""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    db = get_conn()
    sensor_types = [
        # humidity legacy + bme
        'dht11_humidity_pct', 'bme280_humidity_pct',
        # light
        'bh1750_lux',
        # bme/bmp temps (no dht11 temp)
        'bme280_temp_f','bme280_temp_c','bmp280_temp_f','bmp280_temp_c',
        # pressure/altitude
        'bme280_pressure_hpa','bmp280_pressure_hpa','bme280_altitude_m','bmp280_altitude_m'
    ]
    placeholders = ','.join('?' for _ in sensor_types)
    rows = db.execute(
        f"""
        SELECT sensor_type,
               MIN(reading) AS min_val,
               MAX(reading) AS max_val,
               AVG(reading) AS avg_val
        FROM readings
        WHERE time_inserted >= ? AND sensor_type IN ({placeholders})
        GROUP BY sensor_type
        """,
        (cutoff, *sensor_types)
    ).fetchall()

    out = { 'temp': None, 'humidity': None, 'light': None, 'pressure': None, 'altitude': None }

    def merge_stat(key, min_v, max_v, avg_v):
        if out[key] is None:
            out[key] = {
                'min': float(min_v) if min_v is not None else None,
                'max': float(max_v) if max_v is not None else None,
                'avg': float(avg_v) if avg_v is not None else None,
            }
        else:
            cur = out[key]
            cur['min'] = min([v for v in (cur['min'], min_v) if v is not None]) if (cur['min'] is not None or min_v is not None) else None
            cur['max'] = max([v for v in (cur['max'], max_v) if v is not None]) if (cur['max'] is not None or max_v is not None) else None
            if cur['avg'] is not None and avg_v is not None:
                cur['avg'] = (cur['avg'] + float(avg_v)) / 2.0
            elif cur['avg'] is None:
                cur['avg'] = float(avg_v) if avg_v is not None else None

    for r in rows:
        st = r['sensor_type']
        min_v, max_v, avg_v = r['min_val'], r['max_val'], r['avg_val']
        if st in ('bme280_temp_f','bmp280_temp_f'):
            merge_stat('temp', min_v, max_v, avg_v)
        elif st in ('bme280_temp_c','bmp280_temp_c'):
            fmin = min_v*9/5+32 if min_v is not None else None
            fmax = max_v*9/5+32 if max_v is not None else None
            favg = avg_v*9/5+32 if avg_v is not None else None
            merge_stat('temp', fmin, fmax, favg)
        elif st in ('dht11_humidity_pct','bme280_humidity_pct'):
            merge_stat('humidity', min_v, max_v, avg_v)
        elif st in ('bme280_pressure_hpa','bmp280_pressure_hpa'):
            merge_stat('pressure', min_v, max_v, avg_v)
        elif st in ('bme280_altitude_m','bmp280_altitude_m'):
            merge_stat('altitude', min_v, max_v, avg_v)
        elif st == 'bh1750_lux':
            merge_stat('light', min_v, max_v, avg_v)

    return jsonify(out)

@bp.route('/api/last_seen')
def api_last_seen():
    """Return last seen timestamp per sensor_type and plant (if any), plus age seconds."""
    db = get_conn()
    rows = db.execute(
        """
        SELECT sensor_type, plant_id, MAX(time_inserted) AS last_ts
        FROM readings
        GROUP BY sensor_type, plant_id
        ORDER BY sensor_type, plant_id
        """
    ).fetchall()
    now_utc = datetime.utcnow().replace(tzinfo=ZoneInfo('UTC'))

    def _parse_ts(v):
        if v is None:
            return None
        if hasattr(v, 'isoformat') and not isinstance(v, str):
            return v
        if isinstance(v, str):
            for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f'):
                try:
                    return datetime.strptime(v, fmt)
                except ValueError:
                    continue
            try:
                return datetime.fromisoformat(v)
            except Exception:
                return None
        return None

    results = []
    for r in rows:
        ts = _parse_ts(r['last_ts'])
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ZoneInfo('UTC'))
        age = (now_utc - ts.astimezone(ZoneInfo('UTC'))).total_seconds()
        results.append({
            'sensor_type': r['sensor_type'],
            'plant_id': r['plant_id'],
            'last_ts': ts.isoformat(),
            'age_seconds': int(age) if age is not None else None
        })
    return jsonify({'items': results})

def _folder_size_bytes(path: str) -> int:
    total = 0
    try:
        for root, dirs, files in os.walk(path):
            for name in files:
                fp = os.path.join(root, name)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    continue
    except Exception:
        pass
    return total

@bp.route('/api/storage_usage')
def api_storage_usage():
    cap = current_app.config['CAPTURE_FOLDER']
    tl = current_app.config['TIMELAPSE_FOLDER']
    db_path = current_app.config['DATABASE']
    capture_bytes = _folder_size_bytes(cap)
    timelapse_bytes = _folder_size_bytes(tl)
    try:
        db_bytes = os.path.getsize(db_path)
    except OSError:
        db_bytes = 0
    # Disk usage for the drive containing captures
    usage = shutil.disk_usage(cap)
    return jsonify({
        'capture_bytes': int(capture_bytes),
        'timelapse_bytes': int(timelapse_bytes),
        'db_bytes': int(db_bytes),
        'disk_total_bytes': int(usage.total),
        'disk_used_bytes': int(usage.used),
        'disk_free_bytes': int(usage.free)
    })

@bp.route('/camera')
def camera_page():
    """Render the camera control UI."""
    cam_url = current_app.config.get('CAMERA_BASE_URL', 'http://192.168.50.59')
    return render_template('camera.html', camera_base_url=cam_url)

@bp.route('/camera/api/status')
def camera_status_proxy():
    base = request.args.get('base') or current_app.config.get('CAMERA_BASE_URL', '')
    url = base.rstrip('/') + '/admin/status'
    try:
        r = requests.get(url, timeout=4)
        r.raise_for_status()
        return jsonify(r.json())
    except Exception as e:
        return jsonify({'error': str(e), 'url': url}), 502

@bp.route('/camera/api/set')
def camera_set_proxy():
    base = request.args.get('base') or current_app.config.get('CAMERA_BASE_URL', '')
    # Forward supported query params
    params = {}
    for key in ('framesize','quality','vflip','hmirror'):
        if key in request.args:
            params[key] = request.args.get(key)
    url = base.rstrip('/') + '/admin/set'
    try:
        r = requests.get(url, params=params, timeout=5)
        r.raise_for_status()
        return jsonify({'ok': True, 'message': r.text})
    except Exception as e:
        return jsonify({'error': str(e), 'url': url}), 502

@bp.route('/camera/api/led')
def camera_led_proxy():
    base = request.args.get('base') or current_app.config.get('CAMERA_BASE_URL', '')
    on = request.args.get('on', '0')
    url = base.rstrip('/') + '/admin/led'
    try:
        r = requests.get(url, params={'on': on}, timeout=4)
        r.raise_for_status()
        return jsonify({'ok': True, 'message': r.text})
    except Exception as e:
        return jsonify({'error': str(e), 'url': url}), 502

@bp.route('/camera/api/reboot')
def camera_reboot_proxy():
    base = request.args.get('base') or current_app.config.get('CAMERA_BASE_URL', '')
    url = base.rstrip('/') + '/admin/reboot'
    try:
        r = requests.get(url, timeout=4)
        r.raise_for_status()
        return jsonify({'ok': True, 'message': r.text})
    except Exception as e:
        return jsonify({'error': str(e), 'url': url}), 502

@bp.route('/camera/api/restart_camera')
def camera_restart_cam_proxy():
    base = request.args.get('base') or current_app.config.get('CAMERA_BASE_URL', '')
    url = base.rstrip('/') + '/admin/restart_camera'
    try:
        r = requests.get(url, timeout=6)
        r.raise_for_status()
        return jsonify({'ok': True, 'message': r.text})
    except Exception as e:
        return jsonify({'error': str(e), 'url': url}), 502

@bp.route('/camera/preview.jpg')
def camera_preview_proxy():
    """Proxy snapshot to allow embedding without CORS and cache issues."""
    base = request.args.get('base') or current_app.config.get('CAMERA_BASE_URL', '')
    url = base.rstrip('/') + '/snapshot'
    try:
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        # Stream back as image/jpeg
        from flask import Response
        return Response(r.content, content_type='image/jpeg', headers={'Cache-Control':'no-store'})
    except Exception as e:
        return abort(502, description=str(e))
