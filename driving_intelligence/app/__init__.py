"""Driving Intelligence prototype – Flask application factory."""
from __future__ import annotations

import logging
import os
import secrets
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, render_template
from werkzeug.exceptions import HTTPException

from config import Config

from .db import init_db
from .security import init_security
from .services.catalog import (DRIVETRAIN_LABELS, SIZE_LABELS, TAG_LABELS, TRANSMISSION_LABELS,
                               WIDTH_LABELS, load_catalog)

__version__ = "0.8.0"


def _configure_logging(app: Flask) -> None:
    log_dir = Path(app.instance_path) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    root = logging.getLogger()
    root.setLevel(app.config.get("LOG_LEVEL", "INFO"))
    # Avoid duplicate handlers when the factory is called several times (tests, reloader).
    if not any(getattr(h, "_di_handler", False) for h in root.handlers):
        file_handler = RotatingFileHandler(log_dir / "app.log", maxBytes=1_000_000,
                                           backupCount=3, encoding="utf-8")
        file_handler.setFormatter(fmt)
        file_handler._di_handler = True  # type: ignore[attr-defined]
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        stream._di_handler = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)
        root.addHandler(stream)


def _load_secret_key(app: Flask) -> str:
    key_file = Path(app.instance_path) / "secret_key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    try:
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:  # another worker created it concurrently
        return key_file.read_text(encoding="utf-8").strip()
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(key)
    return key


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    _configure_logging(app)
    log = logging.getLogger("app")

    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _load_secret_key(app)
    if not app.config.get("DATABASE"):
        app.config["DATABASE"] = str(Path(app.instance_path) / "driving_intelligence.sqlite3")

    app.extensions["catalog"] = load_catalog()  # fail fast on bad data
    init_db(app)
    init_security(app)

    app.jinja_env.globals.update(
        APP_VERSION=__version__,
        DRIVETRAIN_LABELS=DRIVETRAIN_LABELS,
        TRANSMISSION_LABELS=TRANSMISSION_LABELS,
        SIZE_LABELS=SIZE_LABELS,
        WIDTH_LABELS=WIDTH_LABELS,
        TAG_LABELS=TAG_LABELS,
    )

    from .routes import bp
    app.register_blueprint(bp)

    @app.errorhandler(HTTPException)
    def handle_http(err: HTTPException):
        if err.code and err.code >= 500:
            log.error("HTTP %s: %s", err.code, err.description)
        return render_template("error.html", code=err.code, name=err.name,
                               message=err.description), err.code

    @app.errorhandler(Exception)
    def handle_unexpected(err: Exception):
        log.exception("Unhandled error: %s", err)
        return render_template("error.html", code=500, name="Internal Server Error",
                               message="일시적인 오류가 발생했습니다. 잠시 후 다시 시도하세요."), 500

    log.info("Driving Intelligence %s started", __version__)
    return app
