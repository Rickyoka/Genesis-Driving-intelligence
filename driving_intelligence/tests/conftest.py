import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402

TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture()
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "DATABASE": str(tmp_path / "test.sqlite3"),
        "ROUTE_PROVIDER": "offline",  # no network in unit tests; OSRM is tested with a mock
    })
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def token(client):
    html = client.get("/garage").get_data(as_text=True)
    m = TOKEN_RE.search(html)
    assert m, "csrf token not rendered"
    return m.group(1)


VEHICLE = {
    "nickname": "테스트 카이맨", "maker": "Porsche", "model": "718 Cayman", "engine": "2.0T",
    "power_ps": "300", "torque_nm": "380", "drivetrain": "MR", "transmission": "DCT",
    "weight_kg": "1365", "size_class": "compact",
}
