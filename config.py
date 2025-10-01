import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'you-will-never-guess'
    DATABASE = os.path.join(os.path.dirname(__file__), 'instance', 'plant_monitor.db')
    CAPTURE_FOLDER = os.path.join(os.path.dirname(__file__), 'app', 'capture')
    TIMELAPSE_FOLDER = os.path.join(os.path.dirname(__file__), 'app', 'timelapse')
    CAPTURE_INTERVAL_SECONDS = int(os.getenv('CAPTURE_INTERVAL_SECONDS', '900'))

    # Ensure the instance folder exists
    INSTANCE_PATH = os.path.join(os.path.dirname(__file__), 'instance')
    os.makedirs(INSTANCE_PATH, exist_ok=True)
    os.makedirs(CAPTURE_FOLDER, exist_ok=True)
    os.makedirs(TIMELAPSE_FOLDER, exist_ok=True)

    # Location for sun calculations
    LOCATION_LAT = float(os.getenv('LOCATION_LAT', '34.749571')) 
    LOCATION_LON = float(os.getenv('LOCATION_LON', '-92.356872')) 

    # Added: light cycle defaults (can be overridden in settings during runtime)
    LIGHT_ON_HOUR = int(os.getenv('LIGHT_ON_HOUR', '19'))  # 8 PM
    LIGHT_OFF_HOUR = int(os.getenv('LIGHT_OFF_HOUR', '09'))  # 11 AM
    # New: minute precision for light schedule
    LIGHT_ON_MINUTE = int(os.getenv('LIGHT_ON_MINUTE', '26'))
    LIGHT_OFF_MINUTE = int(os.getenv('LIGHT_OFF_MINUTE', '28'))

    # ESP32 Camera default base URL (can be overridden via query param in proxies)
    CAMERA_BASE_URL = os.getenv('CAMERA_BASE_URL', 'http://192.168.50.59')
