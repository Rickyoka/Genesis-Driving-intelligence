"""Entry point: `python run.py` (development) or `waitress-serve --call app:create_app` (production)."""
import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    debug = os.environ.get("DI_DEBUG", "0") == "1"
    host = os.environ.get("DI_HOST", "127.0.0.1")
    port = int(os.environ.get("DI_PORT", "5000"))
    app.run(host=host, port=port, debug=debug)
