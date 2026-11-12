"""Application configuration (overridable via environment variables)."""
from __future__ import annotations

import os


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    # Secret key: set DI_SECRET_KEY in production. If absent, a random key is
    # generated once and persisted to instance/secret_key (see app/__init__.py).
    SECRET_KEY = os.environ.get("DI_SECRET_KEY")

    # SQLite file inside the Flask instance folder unless overridden.
    DATABASE = os.environ.get("DI_DATABASE")

    # Upload limit for drive-log CSVs (bytes). Larger requests return 413.
    MAX_CONTENT_LENGTH = int(os.environ.get("DI_MAX_UPLOAD_BYTES", 8 * 1024 * 1024))

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _env_bool("DI_COOKIE_SECURE", False)  # True behind HTTPS
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 7

    JSON_AS_ASCII = False
    TEMPLATES_AUTO_RELOAD = False
    SEND_FILE_MAX_AGE_DEFAULT = 60 * 60 * 24  # static cache: 1 day

    LOG_LEVEL = os.environ.get("DI_LOG_LEVEL", "INFO").upper()

    # Demo mode: enables "arrival simulation" stamps and the stamp/points reset button,
    # so the stamp tour can be presented without travelling. Set DI_DEMO_MODE=0 in production.
    DEMO_MODE = _env_bool("DI_DEMO_MODE", True)

    # Route geometry provider for the route guide: "osrm" (map/routing API, needs internet)
    # or "offline" (synthetic demo geometry only). OSRM failures fall back to offline automatically.
    ROUTE_PROVIDER = os.environ.get("DI_ROUTE_PROVIDER", "osrm").strip().lower()
    OSRM_URL = os.environ.get(
        "DI_OSRM_URL",
        "https://router.project-osrm.org/route/v1/driving/{coords}?overview=full&geometries=geojson&steps=true")
    # Map tiles shown by the browser (must also be allowed in the CSP img-src below).
    MAP_TILE_URL = os.environ.get("DI_MAP_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
    MAP_ATTRIBUTION = os.environ.get("DI_MAP_ATTRIBUTION", "© OpenStreetMap contributors")
