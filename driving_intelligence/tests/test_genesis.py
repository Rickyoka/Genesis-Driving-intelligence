"""Tests for the Genesis concept features (v0.2): EV, learned navigation, HUD, Data Lab, intro."""
import json
import re

from conftest import VEHICLE

from app.services import datalab, learning
from app.services.catalog import load_catalog
from app.services.hud import build_segments
from app.services.scoring import MODES, VehicleProfile, score_course

CAT = load_catalog()
MAGMA = VehicleProfile(609, 740, 2250, "AWD", "EV", "compact")
EV_VEHICLE = {**VEHICLE, "nickname": "GV60 Magma", "maker": "Genesis", "model": "GV60 Magma",
              "power_ps": "609", "torque_nm": "740", "drivetrain": "AWD", "transmission": "EV",
              "weight_kg": "2250"}


# ---------------------------------------------------------------- services
def test_presets_are_genesis_and_include_magma():
    assert all(p["maker"] == "Genesis" for p in CAT.presets)
    assert any(p["model"] == "GV60 Magma" and p["transmission"] == "EV" for p in CAT.presets)


def test_ev_guidance_mentions_regen_and_track_only_features():
    s = score_course(CAT.courses_by_id["hangyeryeong"], "sport", MAGMA)
    joined = " ".join(s.notes)
    assert "회생제동" in joined and "폐쇄 서킷" in joined


def test_personalization_shifts_weights_and_keeps_sum():
    weak = [{"braking": {"score": 55}, "cornering": {"score": 80}, "accel": {"score": 80}}]
    p = learning.build(weak, [CAT.courses_by_id["jianjae"], CAT.courses_by_id["odojae"]])
    assert p.active and p.weight_shift["safety"] > 0 and p.tag_bonus
    for mode in MODES.values():
        w = learning.apply_shift(mode["weights"], p.weight_shift)
        assert abs(sum(w.values()) - 1.0) < 1e-9 and all(v >= 0 for v in w.values())


def test_personalization_prefers_safer_roads_for_weak_braking():
    risky, safe = CAT.courses_by_id["misiryeong-old"], CAT.courses_by_id["donghae-7"]
    weak = learning.build([{"braking": {"score": 50}}], [])
    gap_plain = score_course(safe, "sport").score - score_course(risky, "sport").score
    gap_learn = (score_course(safe, "sport", personal=weak).score
                 - score_course(risky, "sport", personal=weak).score)
    assert gap_learn > gap_plain


def test_empty_personalization_is_inactive():
    assert not learning.build([], []).active


def test_hud_segments_deterministic_and_within_limit():
    c = CAT.courses_by_id["manhangjae"]
    a, b = build_segments(c, MAGMA), build_segments(c, MAGMA)
    assert a == b and 5 <= len(a) <= 12
    assert all(s["speed"] < s["limit"] for s in a)
    assert any("회생제동" in s["hint"] for s in a)


def test_fleet_aggregate_and_insights():
    key = tuple((p["model"], p["power_ps"], p["weight_kg"], p["transmission"]) for p in CAT.presets)
    agg = datalab.aggregate(datalab.synthetic_fleet(key))
    assert agg["n"] == datalab.FLEET_SIZE
    assert all(m["n"] >= datalab.MIN_GROUP for m in agg["models"])
    assert abs(sum(t["share"] for t in agg["tags"]) - 100) <= 3
    assert len(datalab.insights(agg)) >= 4
    assert datalab.aggregate([]) == {"n": 0}


# ---------------------------------------------------------------- routes
def test_new_pages_render(client):
    for url in ("/hud", "/hud?course=jianjae", "/hud?course=bogus", "/ecosystem", "/compare",
                "/datalab", "/courses?learn=0", "/courses?learn=1&learn=0"):
        assert client.get(url).status_code == 200, url


def test_intro_markup_and_assets(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="intro"' in html and "data-start" in html and 'data-powertrain="ice"' in html
    assert "js/boot.js" in html and "js/intro.js" in html
    assert client.get("/static/js/intro.js").status_code == 200


def test_ev_vehicle_switches_intro_to_ev(client, token):
    client.post("/garage", data={**EV_VEHICLE, "csrf_token": token})
    html = client.get("/").get_data(as_text=True)
    assert 'data-powertrain="ev"' in html and "POWER" in html
    hud = client.get("/hud?course=manhangjae").get_data(as_text=True)
    assert "회생제동" in hud


def test_learned_banner_reacts_to_history(client, token):
    client.post("/analyze", data={"use_sample": "1", "csrf_token": token})
    client.post("/courses/jianjae/favorite", data={"csrf_token": token})
    html = client.get("/courses").get_data(as_text=True)
    assert "학습 반영 중" in html and "학습: 선호 유형" in client.get("/courses/odojae").get_data(as_text=True)
    off = client.get("/courses?learn=0").get_data(as_text=True)
    assert "학습 반영 중" not in off


def test_report_shows_wearable_card(client, token):
    client.post("/analyze", data={"use_sample": "1", "csrf_token": token})
    html = client.get("/reports/1").get_data(as_text=True)
    assert "웨어러블 연동" in html and "bpm" in html


def test_datalab_consent_flow_and_export(client, token):
    assert client.get("/datalab/export.json?source=mine").status_code == 403
    fleet = client.get("/datalab/export.json")
    body = json.loads(fleet.data)
    assert body["source"] == "synthetic-demo-fleet" and "scatter" not in body["data"]
    assert "attachment" in fleet.headers["Content-Disposition"]
    assert client.post("/datalab/consent", data={"consent": "1"}).status_code == 400  # CSRF
    client.post("/datalab/consent", data={"consent": "1", "csrf_token": token})
    client.post("/garage", data={**EV_VEHICLE, "csrf_token": token})
    client.post("/analyze", data={"use_sample": "1", "csrf_token": token})
    mine = json.loads(client.get("/datalab/export.json?source=mine").data)
    assert mine["source"] == "user-consented" and mine["data"]["n"] == 1
    raw = json.dumps(mine["data"])
    assert "timestamp" not in raw and "trace" not in raw
    client.post("/datalab/consent", data={"consent": "0", "csrf_token": token})
    assert client.get("/datalab/export.json?source=mine").status_code == 403


def test_no_inline_scripts_or_styles(client):
    for url in ("/", "/hud", "/datalab", "/ecosystem", "/garage"):
        html = client.get(url).get_data(as_text=True)
        assert not re.search(r"<script(?![^>]*\b(src|type=\"application/json\"))", html), url
        assert 'style="' not in html, url
