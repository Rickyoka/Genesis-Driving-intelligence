"""Navigation-linked HUD (v0.6): corner phases, rhythm sections, closed-circuit profile."""
import math

import pytest

from app.services import corner_engine as ce
from app.services import route_guide as rg
from app.services.catalog import load_catalog
from app.services.hud import build_segments

CAT = load_catalog()


def magma():
    mu = ce.TYRES["street"][1]
    return ce.Dynamics(a_lat=0.95 * mu * ce.G, a_brake=0.9 * mu * ce.G, a_traction=0.8 * mu * ce.G,
                       power_w=609 * 735.5, mass_kg=2250)


# ---------------------------------------------------------------- public roads
@pytest.mark.parametrize("cid", [c.id for c in CAT.courses])
def test_corner_phases_are_ordered_on_every_course(cid):
    co = CAT.courses_by_id[cid]
    res = rg.build_profile(rg.synthetic_route(co, build_segments(co, None)), 50)
    for c in res["corners"]:
        assert c["turnin_m"] <= c["apex_m"] <= c["exit_m"]
        assert c["apex_m"] <= c["throttle_m"] <= c["exit_m"] + 100
        assert c["brake_m"] is None or c["brake_m"] <= c["apex_m"]
        assert c["min_kmh"] <= c["entry_kmh"] <= 50          # never above the limit


def test_hairpin_detected_with_braking():
    co = CAT.courses_by_id["jianjae"]
    res = rg.build_profile(rg.synthetic_route(co, [{"direction": "right", "severity": 3, "distance_m": 400}]), 50)
    pins = [c for c in res["corners"] if c["severity"] == "헤어핀"]
    assert pins and pins[0]["brake_m"] is not None and pins[0]["min_kmh"] <= 32


def test_rhythm_sections_cluster_linked_corners():
    cs = [{"turnin_m": t, "exit_m": t + 60, "brake_m": t - 40, "min_kmh": 35, "entry_kmh": 50} for t in (100, 300, 500)]
    far = [{"turnin_m": 2000, "exit_m": 2060, "brake_m": None, "min_kmh": 50, "entry_kmh": 50}]
    sec = ce.rhythm_sections(cs + far, 50)
    assert len(sec) == 1 and sec[0]["count"] == 3 and sec[0]["from_m"] == 60
    assert sec[0]["speed_band"][1] <= 50
    assert ce.rhythm_sections(cs[:2], 50) == []


def test_s_bend_splits_into_two_corners():
    co = CAT.courses_by_id["jianjae"]
    geo = rg.synthetic_route(co, [{"direction": "left", "severity": 2, "distance_m": 100},
                                  {"direction": "right", "severity": 2, "distance_m": 75}])
    dirs = [c["dir"] for c in rg.build_profile(geo, 60)["corners"]]
    assert -1 in dirs and 1 in dirs


# ---------------------------------------------------------------- circuit
def test_layout_closes_exactly_and_keeps_radii():
    pts, names = ce.build_layout()
    assert math.dist(pts[0], pts[-1]) < 1e-6
    assert [n["name"].split()[0] for n in names] == ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9"]


def test_circuit_profile_physics():
    dyn = magma()
    r = ce.circuit_profile(dyn, rg.curvature)
    assert len(r["corners"]) == 9
    assert 60 < r["lap_time_s"] < 150 and 150 < r["vmax_kmh"] <= 300
    for (m1, v1), (m2, v2) in zip(r["profile"], r["profile"][1:]):
        if m2 > m1:
            a = ((v2 / 3.6) ** 2 - (v1 / 3.6) ** 2) / (2 * (m2 - m1))
            assert a >= -dyn.a_brake - 0.3          # never brakes harder than the tyres allow
    pin = next(c for c in r["corners"] if c["name"].startswith("T5"))
    assert pin["severity"] == "헤어핀" and pin["brake_m"] is not None and pin["min_kmh"] < 70
    assert all(r["cues"][i]["at_m"] <= r["cues"][i + 1]["at_m"] for i in range(len(r["cues"]) - 1))
    kinds = {q["kind"] for q in r["cues"]}
    assert {"board", "brake", "turnin", "apex", "throttle", "full"} <= kinds


def test_grippier_tyres_and_more_power_are_faster():
    base = ce.circuit_profile(magma(), rg.curvature)["lap_time_s"]
    mu = ce.TYRES["semi"][1]
    semi = ce.Dynamics(a_lat=0.95 * mu * ce.G, a_brake=0.9 * mu * ce.G, a_traction=0.8 * mu * ce.G,
                       power_w=609 * 735.5, mass_kg=2250)
    weak = ce.Dynamics(a_lat=magma().a_lat, a_brake=magma().a_brake, a_traction=magma().a_traction,
                       power_w=200 * 735.5, mass_kg=2250)
    assert ce.circuit_profile(semi, rg.curvature)["lap_time_s"] < base
    assert ce.circuit_profile(weak, rg.curvature)["lap_time_s"] > base


# ---------------------------------------------------------------- routes
def test_drive_pages(client):
    road = client.get("/drive?mode=road&course=jianjae")
    assert road.status_code == 200 and "리듬 모드" in road.get_data(as_text=True)
    circ = client.get("/drive?mode=circuit").get_data(as_text=True)
    assert "data-gate-ok" in circ and "폐쇄된 서킷 전용" in circ
    assert 'data-play disabled' in circ                    # locked until the closed-course check
    assert client.get("/drive?mode=bogus").status_code == 200
    assert client.get("/static/js/drive.js").status_code == 200


def test_circuit_api(client):
    j = client.get("/api/circuit-guide?tyre=semi").get_json()
    assert j["tyre"] == "semi" and len(j["corners"]) == 9 and j["map"]
    assert client.get("/api/circuit-guide?tyre=racing").status_code == 400


def test_route_api_exposes_corners_and_rhythm(client):
    j = client.get("/api/route-guide?course=jianjae").get_json()
    assert "corners" in j and "rhythm" in j and len(j["curv"]) == len(j["profile"])
