"""Route guide (v0.4): map-API geometry -> decel / stop / accel zones."""
import io
import json
import math
import urllib.error

import pytest

from app.services import route_guide as rg
from app.services.catalog import load_catalog
from app.services.hud import build_segments

CAT = load_catalog()


def _line(lat0, lng0, heading_deg, length_m, step=20):
    pts, h = [], math.radians(heading_deg)
    for k in range(int(length_m // step) + 1):
        d = k * step
        pts.append((lat0 + d * math.cos(h) / 111_320, lng0 + d * math.sin(h) / (111_320 * math.cos(math.radians(lat0)))))
    return pts


def _osrm_doc():
    """Realistic OSRM response: 1 km north, right turn at a junction, 1 km east."""
    a = _line(37.20, 128.85, 0, 1000)
    b = _line(a[-1][0], a[-1][1], 90, 1000)[1:]
    coords = [[lng, lat] for lat, lng in a + b]
    return {"code": "Ok", "routes": [{"distance": 2000.0, "duration": 150.0,
            "geometry": {"type": "LineString", "coordinates": coords},
            "legs": [{"steps": [
                {"name": "", "maneuver": {"type": "depart", "location": coords[0]}},
                {"name": "함백산로", "maneuver": {"type": "turn", "modifier": "right", "location": [a[-1][1], a[-1][0]]}},
                {"name": "", "maneuver": {"type": "arrive", "location": coords[-1]}}]}]}]}


# ---------------------------------------------------------------- physics
def test_profile_respects_limit_and_comfort_bounds():
    geo = rg.parse_osrm(_osrm_doc())
    res = rg.build_profile(geo, 60, "standard")
    speeds = [v for _, v in res["profile"]]
    assert max(speeds) <= 60.0 + 1e-6
    assert res["profile"][0][1] == 0 and res["profile"][-1][1] == 0  # stop at start and end
    # deceleration / acceleration never steeper than the comfort constants
    for (m1, v1), (m2, v2) in zip(res["profile"], res["profile"][1:]):
        if m2 == m1:
            continue
        a = ((v2 / 3.6) ** 2 - (v1 / 3.6) ** 2) / (2 * (m2 - m1))
        assert -rg.DECEL - 0.05 <= a <= rg.ACCEL + 0.05


def test_turn_junction_creates_decel_then_accel():
    res = rg.build_profile(rg.parse_osrm(_osrm_doc()), 60, "standard")
    kinds = [g["kind"] for g in res["guidance"]]
    assert kinds[0] == "stop" and kinds[-1] == "stop"
    turn = [g for g in res["guidance"] if g["kind"] == "decel" and "우회전 교차로" in g["detail"]]
    assert turn and turn[0]["target_kmh"] <= 20
    i = res["guidance"].index(turn[0])
    assert "accel" in kinds[i + 1:]


def test_curve_speed_matches_physics():
    # a 70 m-radius curve at 0.20 g -> sqrt(0.2*9.80665*70) = 11.7 m/s = 42 km/h
    co = CAT.courses_by_id["manhangjae"]
    geo = rg.synthetic_route(co, [{"direction": "right", "severity": 2, "distance_m": 400}])
    res = rg.build_profile(geo, 80, "standard")
    curve_min = min(v for m, v in res["profile"] if 300 < m < res["distance_m"] - 300)
    assert 38 <= curve_min <= 46
    relaxed = rg.build_profile(geo, 80, "relaxed")
    assert min(v for m, v in relaxed["profile"] if 300 < m < relaxed["distance_m"] - 300) < curve_min


def test_zones_cover_route_and_shares_sum():
    co = CAT.courses_by_id["jianjae"]
    res = rg.build_profile(rg.synthetic_route(co, build_segments(co, None)), 50, "standard")
    assert res["zones"][0]["from_m"] == 0 and res["zones"][-1]["to_m"] == res["distance_m"]
    assert 97 <= sum(res["shares"].values()) <= 103


def test_ev_wording():
    res = rg.build_profile(rg.parse_osrm(_osrm_doc()), 60, "standard", powertrain="ev")
    assert any("회생제동" in g["detail"] for g in res["guidance"] if g["kind"] == "decel")


@pytest.mark.parametrize("comfort,limit", [("sport", 60), ("standard", 150), ("standard", 5)])
def test_profile_rejects_unsafe_settings(comfort, limit):
    with pytest.raises(rg.RouteError):
        rg.build_profile(rg.parse_osrm(_osrm_doc()), limit, comfort)


# ---------------------------------------------------------------- parsing / validation
def test_waypoint_param_validation():
    assert len(rg.parse_waypoint_param("37.2,128.8;37.1,128.9")) == 2
    for bad in ("", "37.2,128.8", "abc", "10,10;20,20", "37.2,128.8;" * 8, "nan,128;37,128"):
        with pytest.raises(rg.RouteError):
            rg.parse_waypoint_param(bad)


def test_parse_osrm_errors():
    with pytest.raises(rg.RouteError):
        rg.parse_osrm({"code": "NoRoute", "routes": []})


def test_every_course_has_route_waypoints():
    for c in CAT.courses:
        assert len(c.waypoints()) == 3, c.id


# ---------------------------------------------------------------- provider (mocked network)
class _Resp(io.BytesIO):
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


def test_osrm_provider_fetches_then_caches(tmp_path, monkeypatch):
    calls = []
    def fake_urlopen(req, timeout):
        calls.append(req.full_url)
        assert req.full_url.startswith("https://router.project-osrm.org/route/v1/driving/128.850000,37.200000;")
        return _Resp(json.dumps(_osrm_doc()).encode())
    monkeypatch.setattr(rg.urllib.request, "urlopen", fake_urlopen)
    p = rg.OsrmProvider(tmp_path)
    wps = [(37.2, 128.85), (37.209, 128.862)]
    assert p.route(wps).source == "osrm"
    assert p.route(wps).source == "osrm"
    assert len(calls) == 1  # second call served from cache


def test_api_falls_back_when_map_api_unreachable(app, client, monkeypatch):
    app.config["ROUTE_PROVIDER"] = "osrm"
    def boom(*a, **k):
        raise urllib.error.URLError("offline")
    monkeypatch.setattr(rg.urllib.request, "urlopen", boom)
    j = client.get("/api/route-guide?course=manhangjae").get_json()
    assert j["source"] == "synthetic" and "연결할 수 없어" in j["fallback_reason"]
    assert j["guidance"] and j["zones"]


def test_backoff_skips_api_after_network_failure(app, client, monkeypatch):
    app.config["ROUTE_PROVIDER"] = "osrm"
    calls = []
    def boom(*a, **k):
        calls.append(1)
        raise urllib.error.URLError("offline")
    monkeypatch.setattr(rg.urllib.request, "urlopen", boom)
    client.get("/api/route-guide?course=manhangjae")
    j = client.get("/api/route-guide?course=jianjae").get_json()
    assert len(calls) == 1 and j["source"] == "synthetic"


def test_api_with_mocked_osrm(app, client, monkeypatch):
    app.config["ROUTE_PROVIDER"] = "osrm"
    monkeypatch.setattr(rg.urllib.request, "urlopen", lambda req, timeout: _Resp(json.dumps(_osrm_doc()).encode()))
    j = client.get("/api/route-guide?course=manhangjae&wp=37.2,128.85;37.209,128.862&limit=60").get_json()
    assert j["source"] == "osrm" and j["fallback_reason"] is None
    assert any(g["kind"] == "decel" for g in j["guidance"])


def test_api_validation(client):
    assert client.get("/api/route-guide?course=nope").status_code == 404
    assert client.get("/api/route-guide?course=manhangjae&wp=1,2;3,4").status_code == 400
    assert client.get("/api/route-guide?course=manhangjae&limit=abc").status_code == 400
    assert client.get("/api/route-guide?course=manhangjae&comfort=track").status_code == 400


def test_route_page_and_csp(client):
    r = client.get("/route?course=jianjae")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "vendor/leaflet/leaflet.js" in html and 'id="route-config"' in html
    assert "img-src 'self' data: https://tile.openstreetmap.org" in r.headers["Content-Security-Policy"]
    assert client.get("/static/vendor/leaflet/leaflet.js").status_code == 200


def test_csp_rejects_non_https_tile_origin():
    from app.security import build_csp
    assert "http://" not in build_csp("http://evil.example/{z}/{x}/{y}.png")
    assert "evil" not in build_csp("https://evil.example'; script-src *;/{z}.png")
