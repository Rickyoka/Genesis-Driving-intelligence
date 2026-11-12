import pytest

from app.services.academy import compute_badges, recommend
from app.services.analysis import AnalysisError, analyze, build_trace, decode_upload, parse_csv
from app.services.catalog import load_catalog
from app.services.scoring import MODES, VehicleProfile, rank_courses, score_course

CAT = load_catalog()
CAYMAN = VehicleProfile(300, 380, 1365, "MR", "DCT", "compact")
G80 = VehicleProfile(304, 422, 1890, "AWD", "AT", "large")


def _csv(rows, header="timestamp_s,speed_kmh,accel_long_g,accel_lat_g"):
    return header + "\n" + "\n".join(",".join(str(x) for x in r) for r in rows)


# ------------------------------------------------------------------ catalog
def test_catalog_loads_and_ids_unique():
    assert len(CAT.courses) >= 15
    assert len({c.id for c in CAT.courses}) == len(CAT.courses)
    assert CAT.presets and CAT.programs


# ------------------------------------------------------------------ scoring
@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize("weather", ["clear", "rain", "snow"])
@pytest.mark.parametrize("night", [False, True])
def test_scores_always_in_range(mode, weather, night):
    for vehicle in (None, CAYMAN, G80):
        for c in CAT.courses:
            s = score_course(c, mode, vehicle, weather, night)
            assert 0 <= s.score <= 100
            assert all(0 <= v <= 100 for v in s.components.values())


def test_weights_sum_to_one():
    for m in MODES.values():
        assert abs(sum(m["weights"].values()) - 1.0) < 1e-9


def test_snow_excludes_winter_caution_roads_and_sorts_them_last():
    ranked = rank_courses(CAT.courses, "sport", CAYMAN, "snow", False)
    flags = [s.excluded_reason is not None for s in ranked]
    assert any(flags)
    assert flags == sorted(flags)  # excluded ones all at the end
    assert all(not s.recommended for s in ranked if s.excluded_reason)


def test_heavy_car_penalised_on_narrow_road():
    jian = CAT.courses_by_id["jianjae"]
    assert score_course(jian, "sport", G80).score < score_course(jian, "sport", CAYMAN).score


def test_rain_lowers_score():
    c = CAT.courses_by_id["manhangjae"]
    assert score_course(c, "sport", CAYMAN, "rain").score < score_course(c, "sport", CAYMAN, "clear").score


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        score_course(CAT.courses[0], "drag")


def test_speed_is_never_rewarded():
    # A more powerful version of the same car must not score higher.
    c = CAT.courses_by_id["hangyeryeong"]
    stronger = VehicleProfile(600, 700, 1365, "MR", "DCT", "compact")
    assert score_course(c, "sport", stronger).score <= score_course(c, "sport", CAYMAN).score


# ------------------------------------------------------------------ analysis
def test_sample_analysis():
    from pathlib import Path
    raw = (Path(__file__).resolve().parent.parent / "app/static/sample/sample_drive.csv").read_bytes()
    r = analyze(parse_csv(decode_upload(raw)))
    assert 0 <= r["overall"] <= 100
    assert r["metrics"]["following"]["score"] is None
    assert r["events"]["harsh_brake"] >= 1
    assert r["weakest"] == "braking"
    assert r["trace"]["points"]


def test_missing_required_column():
    with pytest.raises(AnalysisError):
        parse_csv("time,speed\n1,2\n")


def test_empty_file():
    with pytest.raises(AnalysisError):
        parse_csv("")


def test_too_few_rows():
    with pytest.raises(AnalysisError):
        parse_csv(_csv([(i, 50, 0, 0) for i in range(5)]))


def test_too_many_bad_rows():
    rows = [(i, 50, 0, 0) for i in range(100)] + [("x", "y", 0, 0)] * 20
    with pytest.raises(AnalysisError):
        parse_csv(_csv(rows))


def test_short_distance_rejected():
    log = parse_csv(_csv([(i * 0.1, 5, 0, 0) for i in range(100)]))  # ~14 m
    with pytest.raises(AnalysisError):
        analyze(log)


def test_derived_accel_and_no_lateral():
    rows = [(i * 0.2, 60 + 5 * ((i // 50) % 2), "", "") for i in range(1500)]
    log = parse_csv(_csv([(t, v) for t, v, *_ in rows], header="timestamp_s,speed_kmh"))
    r = analyze(log)
    assert r["derived_accel"] is True and r["has_lateral"] is False
    assert r["metrics"]["steering"]["score"] is None
    assert r["metrics"]["cornering"]["score"] is None


def test_non_increasing_time_and_nan_skipped():
    rows = [(i, 60, 0, 0) for i in range(200)]
    rows.insert(50, (10, 60, 0, 0))     # time goes backwards
    rows.insert(60, (300, "nan", 0, 0))  # NaN speed
    log = parse_csv(_csv(rows))
    assert log.skipped == 2


def test_cp949_decoding():
    text = "timestamp_s,speed_kmh\n0,10\n"
    assert decode_upload(text.encode("cp949")).startswith("timestamp_s")


def test_build_trace_single_value():
    tr = build_trace([0.0, 1.0], [0.0, 0.0], [])
    assert tr["points"]


# ------------------------------------------------------------------ academy
def test_recommend_targets_weakest_and_dedupes():
    metrics = {"braking": {"score": 55}, "accel": {"score": 65}, "steering": {"score": None},
               "cornering": {"score": 90}, "following": {"score": None}}
    recs = recommend(CAT.programs, metrics, 70, "MR", "MT")
    ids = [r.program.id for r in recs]
    assert ids[0] in ("defensive", "safe-braking")
    assert len(ids) == len(set(ids)) <= 4


def test_track_intro_only_when_stable():
    good = {k: {"score": 90} for k in ("braking", "accel", "steering", "cornering")}
    assert "track-intro" in [r.program.id for r in recommend(CAT.programs, good, 90)]
    assert "track-intro" not in [r.program.id for r in recommend(CAT.programs, good, 70)]


def test_badges():
    b = {x.key: x.earned for x in compute_badges([60, 66, 70], [50, 85, None], 0)}
    assert b["first"] and b["steady"] and b["smooth-brake"] and b["growth"] and not b["learner"]
    assert not any(x.earned for x in compute_badges([], [], 0))
