import cv2
import os
from datetime import datetime, time as dtime
from flask import current_app
import subprocess, shutil
import requests  # added
import numpy as np  # added
from zoneinfo import ZoneInfo


def _is_within_light_window(now=None):
    """Return True if now (local Central time) is within configured light on/off window.
    Supports schedules that cross midnight.
    """
    tz_name = getattr(current_app.config, 'LOCATION_TZ', 'America/Chicago') if hasattr(current_app.config, 'LOCATION_TZ') else 'America/Chicago'
    tz = ZoneInfo(tz_name)
    now = now or datetime.now(tz)
    # Build today's on/off times
    on_h = int(current_app.config.get('LIGHT_ON_HOUR', 20))
    off_h = int(current_app.config.get('LIGHT_OFF_HOUR', 11))
    on_m = int(current_app.config.get('LIGHT_ON_MINUTE', 0))
    off_m = int(current_app.config.get('LIGHT_OFF_MINUTE', 0))
    on = dtime(hour=max(0, min(23, on_h)), minute=max(0, min(59, on_m)))
    off = dtime(hour=max(0, min(23, off_h)), minute=max(0, min(59, off_m)))
    now_t = now.time()

    if on <= off:
        # Same-day window, e.g., 08:00-20:00
        return on <= now_t <= off
    else:
        # Overnight window crossing midnight, e.g., 20:00-11:00
        return now_t >= on or now_t <= off


def capture_frame():
    # Gate by light schedule
    if not _is_within_light_window():
        raise RuntimeError('Skipped capture: outside light-on window')

    # Fetch a snapshot from remote camera endpoint instead of local webcam
    url = current_app.config.get('SNAPSHOT_URL', 'http://192.168.50.101:8000/snapshot')
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
    except Exception as e:
        raise RuntimeError(f'Failed to fetch snapshot: {e}')

    content_type = resp.headers.get('Content-Type', '')
    data = resp.content
    # Try to decode image bytes (validate)
    np_arr = np.frombuffer(data, dtype=np.uint8)
    frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError('Snapshot response is not a valid image')

    ts = datetime.utcnow()
    date_folder = ts.strftime('%Y-%m-%d')
    folder_path = os.path.join(current_app.config['CAPTURE_FOLDER'], date_folder)
    os.makedirs(folder_path, exist_ok=True)
    filename = f"frame_{ts.strftime('%Y%m%d_%H%M%S')}.jpg"
    path = os.path.join(folder_path, filename)
    # Save validated frame
    if not cv2.imwrite(path, frame):
        raise RuntimeError('Failed to write image to disk')
    return path


def _list_all_frames():
    root = current_app.config['CAPTURE_FOLDER']
    frames = []
    if not os.path.isdir(root):
        return frames
    for day in sorted(os.listdir(root)):
        day_path = os.path.join(root, day)
        if os.path.isdir(day_path):
            for f in sorted(os.listdir(day_path)):
                if f.endswith('.jpg') and f.startswith('frame_'):
                    frames.append(os.path.join(day_path, f))
    return frames


def build_timelapse(output_name='timelapse_latest.mp4', fps=30):
    images = _list_all_frames()
    if not images:
        raise RuntimeError('No images to build timelapse')
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg:
        pattern_file = os.path.join(current_app.config['TIMELAPSE_FOLDER'], 'frames_list.txt')
        with open(pattern_file, 'w') as f:
            for img in images:
                f.write(f"file '{img}'\n")
        ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        mp4_name = output_name if output_name.endswith('.mp4') else f'timelapse_{ts}.mp4'
        mp4_path = os.path.join(current_app.config['TIMELAPSE_FOLDER'], mp4_name)
        cmd = [ffmpeg, '-y', '-f', 'concat', '-safe', '0', '-i', pattern_file,
               '-r', str(fps), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', mp4_path]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            webm_path = mp4_path.replace('.mp4', '.webm')
            cmd_webm = [ffmpeg, '-y', '-f', 'concat', '-safe', '0', '-i', pattern_file,
                        '-r', str(fps), '-c:v', 'libvpx-vp9', '-b:v', '0', '-crf', '35', webm_path]
            subprocess.run(cmd_webm, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return mp4_path
        except Exception as e:
            current_app.logger.warning(f"ffmpeg build failed ({e}), falling back to OpenCV.")
    # Fallback OpenCV
    first = cv2.imread(images[0])
    height, width, _ = first.shape
    output_path = os.path.join(current_app.config['TIMELAPSE_FOLDER'], output_name)
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    video = cv2.VideoWriter(output_path, fourcc, fps, (width, height))
    for img in images:
        frame = cv2.imread(img)
        video.write(frame)
    video.release()
    return output_path


def get_latest_image():
    frames = _list_all_frames()
    if not frames:
        return None
    latest = max(frames, key=os.path.getmtime)
    # return relative path segment after CAPTURE_FOLDER for URL building
    root = current_app.config['CAPTURE_FOLDER']
    rel = os.path.relpath(latest, root)
    return rel
