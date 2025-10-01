import os
from flask import Flask
from threading import Thread
import time
from . import db
from config import Config


def _start_periodic_capture(app):
    from .camera import capture_frame
    def worker():
        with app.app_context():
            while True:
                interval = int(app.config.get('CAPTURE_INTERVAL_SECONDS', 900))
                try:
                    capture_frame()
                except Exception as e:
                    app.logger.error(f"Periodic capture error: {e}")
                time.sleep(max(60, interval))  # safety lower bound 60s
    t = Thread(target=worker, daemon=True)
    t.start()


def create_app(config_class=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)

    # ensure the instance folder exists
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass

    db.init_app(app)

    from .routes import bp as main_bp  # import after app config
    app.register_blueprint(main_bp)

    # Avoid starting twice in debug reloader
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        _start_periodic_capture(app)

    return app
