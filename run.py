from app import create_app, db
import os

app = create_app()

if __name__ == '__main__':
    with app.app_context():
        # Create database tables if they don't exist
        if not os.path.exists(app.config['DATABASE']):
            db.init_db()
    app.run(host='0.0.0.0', port=5001, debug=True)
