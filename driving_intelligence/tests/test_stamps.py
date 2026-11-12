"""Drive-stamp tour & Genesis points (v0.3)."""
import sqlite3
from datetime import datetime, timedelta

import pytest

from app import create_app
from app.services import stamps
from app.services.catalog import load_catalog

CAT = load_catalog()
ALL = [c.id for c in CAT.courses]
T0 = datetime(2026, 11, 7, 9, 0)


@pytest.fixture()
def conn(app):
    c = sqlite3.connect(app.config["DATABASE"])
    yield c
    c.close()


def cp(cid):
    return CAT.courses_by_id[cid].checkpoint


# ---------------------------------------------------------------- data integrity
def test_every_course_has_checkpoint_and_sets_reference_real_courses():
    assert all(c.checkpoint is not None for c in CAT.courses)
    for s in stamps.SETS:
        assert set(s.course_ids) <= set(ALL), s.key


def test_haversine_known_distance():
    # Seoul City Hall -> Busan City Hall is roughly 325 km in a straight line
    d = stamps.haversine_m(37.5663, 126.9779, 35.1798, 129.0750)
    assert 320_000 < d < 330_000
    assert stamps.haversine_m(37.5, 127.0, 37.5, 127.0) == 0


# ---------------------------------------------------------------- geofence rules
def test_checkin_inside_radius_stamps_and_pays(conn):
    p = cp("hangyeryeong")
    r = stamps.check_in(conn, CAT.courses, p.lat + 0.005, p.lng, 25, now=T0)  # ~556 m north
    assert r["status"] == "stamped" and r["balance"] == stamps.STAMP_POINTS


def test_checkin_outside_radius(conn):
    r = stamps.check_in(conn, CAT.courses, 36.35, 127.38, 20, now=T0)  # Daejeon: no checkpoint
    assert r["status"] == "outside" and r["nearest"] and r["distance_km"] > 3
    assert stamps.balance(conn) == 0


@pytest.mark.parametrize("lat,lng,acc", [(91, 0, 10), (37.5, 127, 900), (float("nan"), 127, 10), (37.5, 127, -1)])
def test_bad_fixes_rejected(conn, lat, lng, acc):
    with pytest.raises(stamps.CheckinError):
        stamps.check_in(conn, CAT.courses, lat, lng, acc, now=T0)


def test_duplicate_within_window_is_not_a_new_visit(conn):
    p = cp("manhangjae")
    stamps.check_in(conn, CAT.courses, p.lat, p.lng, 20, now=T0)
    r = stamps.check_in(conn, CAT.courses, p.lat, p.lng, 20, now=T0 + timedelta(minutes=3))
    assert r["status"] == "already"
    assert conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0] == 1
    r = stamps.check_in(conn, CAT.courses, p.lat, p.lng, 20, now=T0 + timedelta(hours=3))
    assert r["status"] == "revisit" and r["balance"] == stamps.STAMP_POINTS  # revisits pay nothing


def test_impossible_speed_rejected_plausible_accepted(conn):
    a, b = cp("hangyeryeong"), cp("jeju-1100")
    stamps.check_in(conn, CAT.courses, a.lat, a.lng, 20, now=T0)
    with pytest.raises(stamps.CheckinError):
        stamps.check_in(conn, CAT.courses, b.lat, b.lng, 20, now=T0 + timedelta(minutes=20))
    r = stamps.check_in(conn, CAT.courses, b.lat, b.lng, 20, now=T0 + timedelta(hours=8))
    assert r["status"] == "stamped"


def test_no_coordinates_stored(conn):
    p = cp("dalmaji")
    stamps.check_in(conn, CAT.courses, p.lat, p.lng, 20, now=T0)
    for table in ("visits", "stamps", "points_ledger"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        assert not {"lat", "lng", "latitude", "longitude", "accuracy"} & set(cols), table


# ---------------------------------------------------------------- points
def test_set_bonus_milestones_and_grand_tour_paid_exactly_once(conn):
    for cid in ALL:
        stamps.record_visit(conn, CAT.courses_by_id[cid], "demo", ALL)
    for cid in ALL:  # second lap must not pay anything
        stamps.record_visit(conn, CAT.courses_by_id[cid], "demo", ALL)
    expected = (len(ALL) * stamps.STAMP_POINTS + sum(s.bonus for s in stamps.SETS)
                + sum(b for _, _, b in stamps.MILESTONES) + stamps.GRAND_TOUR_BONUS)
    assert stamps.balance(conn) == expected
    refs = [r[0] for r in conn.execute("SELECT ref FROM points_ledger")]
    assert len(refs) == len(set(refs))


def test_set_progress(conn):
    for cid in ("jianjae", "odojae"):
        stamps.record_visit(conn, CAT.courses_by_id[cid], "demo", ALL)
    prog = {p["key"]: p for p in stamps.set_progress(stamps.collected_ids(conn), {})}
    assert prog["jirisan"]["got"] == 2 and not prog["jirisan"]["done"]


def test_reset(conn):
    stamps.record_visit(conn, CAT.courses_by_id["jianjae"], "demo", ALL)
    stamps.reset_all(conn)
    assert stamps.balance(conn) == 0 and not stamps.collected_ids(conn)


# ---------------------------------------------------------------- routes
def test_stamps_page_and_geolocation_policy(client):
    res = client.get("/stamps")
    assert res.status_code == 200
    assert "geolocation=(self)" in res.headers["Permissions-Policy"]
    html = res.get_data(as_text=True)
    assert 'id="checkpoint-data"' in html and "data-autostamp" in html


def test_checkin_endpoint_json(client, token):
    p = cp("jeju-1100")
    r = client.post("/stamps/checkin", data={"csrf_token": token, "lat": p.lat, "lng": p.lng, "accuracy": 15})
    assert r.status_code == 200 and r.get_json()["status"] == "stamped"
    bad = client.post("/stamps/checkin", data={"csrf_token": token, "lat": "x", "lng": p.lng, "accuracy": 15})
    assert bad.status_code == 422 and bad.get_json()["status"] == "rejected"
    assert client.post("/stamps/checkin", data={"lat": p.lat, "lng": p.lng, "accuracy": 15}).status_code == 400


def test_demo_flow_and_ui(client, token):
    r = client.post("/stamps/demo/jianjae", data={"csrf_token": token}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "지안재 전망 포인트 도착 (시연)" in html and "+100P" in html
    assert "스탬프 ✓" in client.get("/courses").get_data(as_text=True)
    assert "스탬프 보유" in client.get("/courses/jianjae").get_data(as_text=True)
    assert "100" in client.get("/").get_data(as_text=True)
    assert client.post("/stamps/demo/nope", data={"csrf_token": token}).status_code == 404
    client.post("/stamps/reset", data={"csrf_token": token})
    assert "스탬프 ✓" not in client.get("/courses").get_data(as_text=True)


def test_demo_disabled_in_production(tmp_path):
    app = create_app({"TESTING": True, "SECRET_KEY": "x", "DEMO_MODE": False,
                      "DATABASE": str(tmp_path / "prod.sqlite3")})
    c = app.test_client()
    html = c.get("/stamps").get_data(as_text=True)
    assert "도착 시뮬레이션" not in html and "초기화" not in html
    import re
    tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/garage").get_data(as_text=True)).group(1)
    assert c.post("/stamps/demo/jianjae", data={"csrf_token": tok}).status_code == 404
    assert c.post("/stamps/reset", data={"csrf_token": tok}).status_code == 404


def test_hud_has_checkpoint_segment(client):
    html = client.get("/hud?course=manhangjae").get_data(as_text=True)
    assert "만항재 정상 도착" in html and "스탬프 자동 적립" in html
