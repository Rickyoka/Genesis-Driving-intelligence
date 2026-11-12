"""HTTP routes for the Driving Intelligence prototype."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date
from pathlib import Path
from urllib.parse import quote

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)

from collections import Counter

from .db import get_db, get_setting, set_setting
from .security import FormError, clean_choice, clean_int, clean_text
import time
import urllib.error

from .services import academy, corner_engine, datalab, learning, media, route_guide, stamps
from .services.analysis import AnalysisError, analyze, decode_upload, parse_csv
from .services.hud import LIMIT_BY_WIDTH, build_segments
from .services.catalog import DRIVETRAINS, SIZE_CLASSES, TAG_LABELS, TRANSMISSIONS, Catalog
from .services.scoring import (COMPONENT_LABELS, MODES, PUBLIC_ROAD_GUIDE, WEATHERS, VehicleProfile,
                               rank_courses, score_course)

bp = Blueprint("main", __name__)
log = logging.getLogger(__name__)

MAX_VEHICLES = 20
ROUTE_BACKOFF_S = 60.0
SAMPLE_DIR = Path(__file__).resolve().parent / "static" / "sample"
SAMPLE_FILE = "sample_drive.csv"


# ----------------------------------------------------------------------------- helpers
def catalog() -> Catalog:
    return current_app.extensions["catalog"]


def list_vehicles() -> list[sqlite3.Row]:
    return get_db().execute("SELECT * FROM vehicles ORDER BY id").fetchall()


def get_vehicle(vid: int) -> sqlite3.Row | None:
    return get_db().execute("SELECT * FROM vehicles WHERE id = ?", (vid,)).fetchone()


def active_vehicle() -> sqlite3.Row | None:
    vid = session.get("vehicle_id")
    row = get_vehicle(vid) if isinstance(vid, int) else None
    if row is None:
        row = get_db().execute("SELECT * FROM vehicles ORDER BY id LIMIT 1").fetchone()
        if row is not None:
            session["vehicle_id"] = row["id"]
        else:
            session.pop("vehicle_id", None)
    return row


def profile_of(row: sqlite3.Row | None) -> VehicleProfile | None:
    if row is None:
        return None
    return VehicleProfile(power_ps=row["power_ps"], torque_nm=row["torque_nm"],
                          weight_kg=row["weight_kg"], drivetrain=row["drivetrain"],
                          transmission=row["transmission"], size_class=row["size_class"])


def favorite_ids() -> set[str]:
    return {r["course_id"] for r in get_db().execute("SELECT course_id FROM favorites")}


def personalization(enabled: bool = True) -> learning.Personalization:
    if not enabled:
        return learning.EMPTY
    db = get_db()
    rows = db.execute("SELECT result_json FROM reports ORDER BY id DESC LIMIT ?",
                      (learning.RECENT_REPORTS,)).fetchall()
    metrics = []
    for r in rows:
        try:
            metrics.append(json.loads(r["result_json"])["metrics"])
        except (ValueError, KeyError, TypeError):
            log.warning("Skipping malformed report JSON in personalization")
    favs = [catalog().courses_by_id[cid] for cid in favorite_ids() if cid in catalog().courses_by_id]
    return learning.build(metrics, favs)


def photo_dir() -> Path:
    return Path(current_app.instance_path) / "uploads" / "vehicles"


def vehicle_image_url(row) -> str:
    """User photo if one was placed, otherwise the original concept rendering."""
    if row is not None and row["photo"] and media.valid_name(row["photo"]):
        return url_for("main.vehicle_photo", name=row["photo"])
    return url_for("static", filename="img/concept-gt.svg")


@bp.app_context_processor
def inject_shell() -> dict:
    """Powertrain of the current vehicle drives the intro sound (engine vs. EV)."""
    try:
        row = active_vehicle()
    except sqlite3.Error:
        log.exception("Active vehicle lookup failed")
        row = None
    return {"shell_vehicle": row, "shell_powertrain": "ev" if row and row["transmission"] == "EV" else "ice",
            "vehicle_image_url": vehicle_image_url}


def read_conditions() -> dict:
    """Whitelist-validated course filter parameters (invalid values fall back to defaults)."""
    args = request.args
    mode = args.get("mode", "sport")
    weather = args.get("weather", "clear")
    tag = args.get("tag", "")
    return {
        "mode": mode if mode in MODES else "sport",
        "weather": weather if weather in WEATHERS else "clear",
        "night": args.get("night") == "1",
        "tag": tag if tag in TAG_LABELS else "",
        "fav": args.get("fav") == "1",
        "learn": args.get("learn", "1") != "0",
    }


# ----------------------------------------------------------------------------- pages
@bp.get("/")
def index():
    vehicle = active_vehicle()
    top = rank_courses(catalog().courses, "sport", profile_of(vehicle), "clear", False, personalization())[:3]
    latest = get_db().execute(
        "SELECT id, overall, distance_km, created_at FROM reports ORDER BY id DESC LIMIT 1").fetchone()
    counts = get_db().execute(
        "SELECT (SELECT COUNT(*) FROM vehicles) AS vehicles, (SELECT COUNT(*) FROM reports) AS reports, "
        "(SELECT COUNT(*) FROM favorites) AS favorites, (SELECT COUNT(*) FROM stamps) AS stamps").fetchone()
    return render_template("index.html", vehicle=vehicle, top=top, latest=latest, counts=counts,
                           course_total=len(catalog().courses), points=stamps.balance(get_db()))


# ---- garage -------------------------------------------------------------------------
@bp.get("/garage")
def garage():
    return render_template("garage.html", vehicles=list_vehicles(), active=active_vehicle(),
                           presets=[dict(p) for p in catalog().presets],
                           preset_note=catalog().preset_note,
                           drivetrains=DRIVETRAINS, transmissions=TRANSMISSIONS, sizes=SIZE_CLASSES)


@bp.post("/garage")
def garage_add():
    f = request.form
    try:
        if len(list_vehicles()) >= MAX_VEHICLES:
            raise FormError(f"차량은 최대 {MAX_VEHICLES}대까지 등록할 수 있습니다.")
        values = (
            clean_text(f.get("nickname"), "차량 별칭", 30),
            clean_text(f.get("maker"), "제조사", 30),
            clean_text(f.get("model"), "모델", 50),
            clean_text(f.get("engine"), "엔진 형식", 50, required=False),
            clean_int(f.get("power_ps"), "최고출력(PS)", 40, 2000),
            clean_int(f.get("torque_nm"), "최대토크(Nm)", 50, 3000),
            clean_choice(f.get("drivetrain"), "구동 방식", DRIVETRAINS),
            clean_choice(f.get("transmission"), "변속기", TRANSMISSIONS),
            clean_int(f.get("weight_kg"), "공차중량(kg)", 500, 4000),
            clean_choice(f.get("size_class"), "차체 크기", SIZE_CLASSES),
        )
    except FormError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.garage"))

    db = get_db()
    cur = db.execute(
        "INSERT INTO vehicles (nickname, maker, model, engine, power_ps, torque_nm, drivetrain, "
        "transmission, weight_kg, size_class) VALUES (?,?,?,?,?,?,?,?,?,?)", values)
    db.commit()
    session["vehicle_id"] = cur.lastrowid
    log.info("Vehicle registered id=%s", cur.lastrowid)
    flash(f"'{values[0]}' 차량을 등록하고 현재 차량으로 선택했습니다.", "ok")
    return redirect(url_for("main.garage"))


@bp.post("/garage/<int:vid>/activate")
def garage_activate(vid: int):
    if get_vehicle(vid) is None:
        abort(404)
    session["vehicle_id"] = vid
    flash("현재 차량을 변경했습니다. 코스 점수가 이 차량 기준으로 다시 계산됩니다.", "ok")
    if request.form.get("back") == "courses":
        return redirect(url_for("main.courses"))
    return redirect(url_for("main.garage"))


@bp.post("/garage/<int:vid>/delete")
def garage_delete(vid: int):
    db = get_db()
    row = get_vehicle(vid)
    if row is None:
        abort(404)
    db.execute("DELETE FROM vehicles WHERE id = ?", (vid,))
    db.commit()
    media.delete_image(photo_dir(), row["photo"])
    if session.get("vehicle_id") == vid:
        session.pop("vehicle_id", None)
    flash("차량을 삭제했습니다.", "ok")
    return redirect(url_for("main.garage"))


@bp.post("/garage/<int:vid>/photo")
def garage_photo(vid: int):
    row = get_vehicle(vid)
    if row is None:
        abort(404)
    try:
        if request.form.get("rights") != "1":
            raise media.MediaError("이미지 사용 권한을 확인해 주세요.")
        upload = request.files.get("photo")
        if upload is None or not upload.filename:
            raise media.MediaError("이미지 파일을 선택하세요.")
        data = media.process_image(upload.read())  # size bounded by MAX_CONTENT_LENGTH
    except media.MediaError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.garage"))
    name = media.save_image(photo_dir(), data)
    db = get_db()
    db.execute("UPDATE vehicles SET photo = ? WHERE id = ?", (name, vid))
    db.commit()
    media.delete_image(photo_dir(), row["photo"])
    log.info("Vehicle photo set id=%s", vid)
    flash(f"'{row['nickname']}' 사진을 배치했습니다. 홈·차고·시동 인트로에 표시됩니다.", "ok")
    return redirect(url_for("main.garage"))


@bp.post("/garage/<int:vid>/photo/delete")
def garage_photo_delete(vid: int):
    row = get_vehicle(vid)
    if row is None:
        abort(404)
    db = get_db()
    db.execute("UPDATE vehicles SET photo = NULL WHERE id = ?", (vid,))
    db.commit()
    media.delete_image(photo_dir(), row["photo"])
    flash("사진을 내리고 기본 컨셉 렌더링으로 되돌렸습니다.", "ok")
    return redirect(url_for("main.garage"))


@bp.get("/media/vehicles/<name>")
def vehicle_photo(name: str):
    if not media.valid_name(name):
        abort(404)
    resp = send_from_directory(photo_dir(), name, mimetype="image/webp", max_age=86400)
    resp.headers["Content-Disposition"] = "inline"
    return resp


# ---- courses ------------------------------------------------------------------------
@bp.get("/courses")
def courses():
    cond = read_conditions()
    vehicle = active_vehicle()
    favs = favorite_ids()
    pool = catalog().courses
    if cond["tag"]:
        pool = tuple(c for c in pool if cond["tag"] in c.tags)
    if cond["fav"]:
        pool = tuple(c for c in pool if c.id in favs)
    personal = personalization(cond["learn"])
    ranked = rank_courses(pool, cond["mode"], profile_of(vehicle), cond["weather"], cond["night"], personal)
    return render_template("courses.html", ranked=ranked, cond=cond, vehicle=vehicle, personal=personal,
                           stamped=stamps.collected_ids(get_db()), stamp_points=stamps.STAMP_POINTS,
                           vehicles=list_vehicles(), favs=favs, modes=MODES, weathers=WEATHERS,
                           tags=TAG_LABELS, course_note=catalog().course_note)


@bp.get("/courses/<course_id>")
def course_detail(course_id: str):
    course = catalog().courses_by_id.get(course_id)
    if course is None:
        abort(404)
    cond = read_conditions()
    vehicle = active_vehicle()
    profile = profile_of(vehicle)
    personal = personalization(cond["learn"])
    current = score_course(course, cond["mode"], profile, cond["weather"], cond["night"], personal)
    by_mode = {m: score_course(course, m, profile, cond["weather"], cond["night"], personal) for m in MODES}
    weights = current.weights
    return render_template("course_detail.html", course=course, cond=cond, vehicle=vehicle,
                           current=current, by_mode=by_mode, modes=MODES, weathers=WEATHERS,
                           weights=weights, component_labels=COMPONENT_LABELS,
                           guide=PUBLIC_ROAD_GUIDE, is_fav=course.id in favorite_ids(), personal=personal,
                           stamp=get_db().execute("SELECT * FROM stamps WHERE course_id = ?", (course.id,)).fetchone(),
                           stamp_points=stamps.STAMP_POINTS, demo_mode=current_app.config["DEMO_MODE"],
                           map_url="https://map.kakao.com/link/search/" + quote(course.name),
                           course_note=catalog().course_note)


@bp.post("/courses/<course_id>/favorite")
def course_favorite(course_id: str):
    if course_id not in catalog().courses_by_id:
        abort(404)
    db = get_db()
    if db.execute("DELETE FROM favorites WHERE course_id = ?", (course_id,)).rowcount == 0:
        db.execute("INSERT INTO favorites (course_id) VALUES (?)", (course_id,))
        flash("코스를 저장했습니다.", "ok")
    else:
        flash("저장한 코스에서 제거했습니다.", "ok")
    db.commit()
    if request.form.get("back") == "detail":
        return redirect(url_for("main.course_detail", course_id=course_id))
    return redirect(url_for("main.courses"))


# ---- analysis -----------------------------------------------------------------------
@bp.get("/analyze")
def analyze_page():
    reports = get_db().execute(
        "SELECT r.id, r.overall, r.distance_km, r.source, r.created_at, v.nickname "
        "FROM reports r LEFT JOIN vehicles v ON v.id = r.vehicle_id ORDER BY r.id DESC LIMIT 10"
    ).fetchall()
    return render_template("analyze.html", reports=reports, vehicle=active_vehicle())


@bp.post("/analyze")
def analyze_submit():
    use_sample = request.form.get("use_sample") == "1"
    try:
        if use_sample:
            raw = (SAMPLE_DIR / SAMPLE_FILE).read_bytes()
            source = "샘플 데이터"
        else:
            upload = request.files.get("logfile")
            if upload is None or not upload.filename:
                raise AnalysisError("분석할 CSV 파일을 선택하세요.")
            if not upload.filename.lower().endswith(".csv"):
                raise AnalysisError("CSV 파일(.csv)만 업로드할 수 있습니다.")
            raw = upload.read()  # bounded by MAX_CONTENT_LENGTH; never written to disk
            source = "업로드 CSV"
        result = analyze(parse_csv(decode_upload(raw)))
    except AnalysisError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.analyze_page"))

    vehicle = active_vehicle()
    db = get_db()
    cur = db.execute(
        "INSERT INTO reports (vehicle_id, source, overall, braking, distance_km, result_json) "
        "VALUES (?,?,?,?,?,?)",
        (vehicle["id"] if vehicle else None, source, result["overall"],
         result["metrics"]["braking"]["score"], result["distance_km"],
         json.dumps(result, ensure_ascii=False, separators=(",", ":"))))
    db.commit()
    log.info("Report created id=%s overall=%s", cur.lastrowid, result["overall"])
    return redirect(url_for("main.report", rid=cur.lastrowid))


@bp.get("/reports/<int:rid>")
def report(rid: int):
    row = get_db().execute(
        "SELECT r.*, v.nickname, v.drivetrain, v.transmission FROM reports r "
        "LEFT JOIN vehicles v ON v.id = r.vehicle_id WHERE r.id = ?", (rid,)).fetchone()
    if row is None:
        abort(404)
    result = json.loads(row["result_json"])
    recs = academy.recommend(catalog().programs, result["metrics"], result["overall"],
                             row["drivetrain"], row["transmission"])
    return render_template("report.html", row=row, r=result, recs=recs,
                           program_note=catalog().program_note)


@bp.post("/reports/<int:rid>/delete")
def report_delete(rid: int):
    db = get_db()
    if db.execute("DELETE FROM reports WHERE id = ?", (rid,)).rowcount == 0:
        abort(404)
    db.commit()
    flash("리포트를 삭제했습니다.", "ok")
    return redirect(url_for("main.analyze_page"))


@bp.get("/sample.csv")
def sample_csv():
    return send_from_directory(SAMPLE_DIR, SAMPLE_FILE, as_attachment=True,
                               download_name="driving_intelligence_sample.csv", mimetype="text/csv")


# ---- academy & passport -------------------------------------------------------------
@bp.get("/academy")
def academy_page():
    latest = get_db().execute(
        "SELECT r.id, r.result_json, v.drivetrain, v.transmission FROM reports r "
        "LEFT JOIN vehicles v ON v.id = r.vehicle_id ORDER BY r.id DESC LIMIT 1").fetchone()
    recs, latest_id = [], None
    if latest is not None:
        res = json.loads(latest["result_json"])
        recs = academy.recommend(catalog().programs, res["metrics"], res["overall"],
                                 latest["drivetrain"], latest["transmission"])
        latest_id = latest["id"]
    return render_template("academy.html", programs=catalog().programs, recs=recs,
                           latest_id=latest_id, program_note=catalog().program_note)


@bp.get("/passport")
def passport():
    db = get_db()
    reports = db.execute("SELECT id, overall, braking, distance_km, created_at FROM reports "
                         "ORDER BY id").fetchall()
    education = db.execute("SELECT * FROM education ORDER BY completed_on DESC, id DESC").fetchall()
    badges = academy.compute_badges([r["overall"] for r in reports],
                                    [r["braking"] for r in reports], len(education))
    have = stamps.collected_ids(db)
    return render_template("passport.html", reports=reports, education=education, badges=badges,
                           stamp_count=len(have), course_total=len(catalog().courses),
                           points=stamps.balance(db),
                           programs=catalog().programs, programs_by_id=catalog().programs_by_id,
                           today=date.today().isoformat())


@bp.post("/passport/education")
def passport_add_education():
    f = request.form
    try:
        program_id = clean_choice(f.get("program_id"), "교육 프로그램", catalog().programs_by_id)
        school = clean_text(f.get("school"), "교육 기관", 60)
        raw_date = clean_text(f.get("completed_on"), "이수일", 10)
        try:
            completed = date.fromisoformat(raw_date)
        except ValueError:
            raise FormError("이수일 형식이 올바르지 않습니다 (YYYY-MM-DD).") from None
        if completed > date.today() or completed.year < 1990:
            raise FormError("이수일은 1990년 이후, 오늘 이전 날짜여야 합니다.")
    except FormError as exc:
        flash(str(exc), "error")
        return redirect(url_for("main.passport"))
    db = get_db()
    # verified stays 0: only a school-side API/admin confirmation may set it (not user input).
    db.execute("INSERT INTO education (program_id, school, completed_on) VALUES (?,?,?)",
               (program_id, school, completed.isoformat()))
    db.commit()
    flash("교육 이수 기록을 추가했습니다 (자기 신고 · 미검증).", "ok")
    return redirect(url_for("main.passport"))


@bp.post("/passport/education/<int:eid>/delete")
def passport_delete_education(eid: int):
    db = get_db()
    if db.execute("DELETE FROM education WHERE id = ?", (eid,)).rowcount == 0:
        abort(404)
    db.commit()
    flash("교육 기록을 삭제했습니다.", "ok")
    return redirect(url_for("main.passport"))


# ---- Drive-stamp tour & Genesis points -------------------------------------------------
@bp.get("/stamps")
def stamps_page():
    db = get_db()
    have_rows = {r["course_id"]: r for r in db.execute("SELECT * FROM stamps")}
    names = {c.id: c.name for c in catalog().courses}
    checkpoints = [{"id": c.id, "name": c.name, "label": c.checkpoint.label, "lat": c.checkpoint.lat,
                    "lng": c.checkpoint.lng, "radius": c.checkpoint.radius_m}
                   for c in catalog().courses if c.checkpoint is not None]
    return render_template("stamps.html", courses=catalog().courses, have=have_rows,
                           sets=stamps.set_progress(set(have_rows), names), points=stamps.balance(db),
                           ledger=stamps.ledger(db), checkpoints=checkpoints,
                           milestones=stamps.MILESTONES, grand_bonus=stamps.GRAND_TOUR_BONUS,
                           stamp_points=stamps.STAMP_POINTS, source_labels=stamps.SOURCE_LABELS,
                           demo_mode=current_app.config["DEMO_MODE"],
                           checkpoint_note=catalog().checkpoint_note)


def _float_field(name: str) -> float:
    try:
        return float(request.form.get(name, ""))
    except ValueError:
        raise stamps.CheckinError("위치 값이 올바르지 않습니다.") from None


@bp.post("/stamps/checkin")
def stamps_checkin():
    """Called by the auto-stamp watcher. Server re-verifies the geofence; coordinates are not stored."""
    try:
        result = stamps.check_in(get_db(), catalog().courses, _float_field("lat"), _float_field("lng"),
                                 _float_field("accuracy"), source="gps")
    except stamps.CheckinError as exc:
        return jsonify(status="rejected", message=str(exc)), 422
    if result["status"] == "stamped":
        log.info("Stamp collected course=%s awards=%d", result["course_id"], len(result["awarded"]))
    return jsonify(result)


@bp.post("/stamps/demo/<course_id>")
def stamps_demo(course_id: str):
    if not current_app.config["DEMO_MODE"]:
        abort(404)
    course = catalog().courses_by_id.get(course_id)
    if course is None or course.checkpoint is None:
        abort(404)
    result = stamps.record_visit(get_db(), course, "demo", [c.id for c in catalog().courses])
    if result["new_stamp"]:
        total = sum(a["amount"] for a in result["awarded"])
        detail = " · ".join(f"{a['reason']} +{a['amount']:,}P" for a in result["awarded"])
        flash(f"📍 {course.checkpoint.label} 도착 (시연) → {detail} (합계 +{total:,}P)", "ok")
    else:
        flash(f"{course.name}: 이미 스탬프를 보유하고 있어 재방문으로 기록했습니다.", "ok")
    if request.form.get("back") == "detail":
        return redirect(url_for("main.course_detail", course_id=course_id))
    return redirect(url_for("main.stamps_page") + "#book")


@bp.post("/stamps/reset")
def stamps_reset():
    if not current_app.config["DEMO_MODE"]:
        abort(404)
    stamps.reset_all(get_db())
    flash("스탬프와 포인트를 초기화했습니다 (시연용).", "ok")
    return redirect(url_for("main.stamps_page"))


# ---- Route guide: map API + decel / stop / accel zones ---------------------------------
def _route_provider():
    if current_app.config.get("ROUTE_PROVIDER") != "osrm":
        return None
    cache = Path(current_app.instance_path) / "route_cache"
    return route_guide.OsrmProvider(cache, current_app.config["OSRM_URL"])


@bp.get("/route")
def route_page():
    cid = request.args.get("course", "")
    course = catalog().courses_by_id.get(cid) or catalog().courses_by_id.get("manhangjae") or catalog().courses[0]
    vehicle = active_vehicle()
    wps = [{"label": lbl, "lat": lat, "lng": lng} for lbl, lat, lng in course.waypoints()]
    return render_template("route.html", course=course, courses=catalog().courses, vehicle=vehicle,
                           waypoints=wps, default_limit=LIMIT_BY_WIDTH[course.width],
                           tile_url=current_app.config["MAP_TILE_URL"],
                           tile_attr=current_app.config["MAP_ATTRIBUTION"],
                           provider=current_app.config.get("ROUTE_PROVIDER"),
                           route_note=catalog().route_note)


@bp.get("/api/route-guide")
def route_guide_api():
    """Read-only JSON: route geometry + advisory speed profile + guidance zones."""
    course = catalog().courses_by_id.get(request.args.get("course", ""))
    if course is None:
        return jsonify(error="알 수 없는 코스입니다."), 404
    comfort = request.args.get("comfort", "standard")
    try:
        limit = float(request.args.get("limit", LIMIT_BY_WIDTH[course.width]))
        raw_wp = request.args.get("wp", "")
        wps = (route_guide.parse_waypoint_param(raw_wp) if raw_wp
               else route_guide.validate_waypoints([(lat, lng) for _, lat, lng in course.waypoints()]))
    except (ValueError, route_guide.RouteError) as exc:
        msg = str(exc) if isinstance(exc, route_guide.RouteError) else "요청 값이 올바르지 않습니다."
        return jsonify(error=msg), 400

    vehicle = active_vehicle()
    powertrain = "ev" if vehicle and vehicle["transmission"] == "EV" else "ice"
    labels = [lbl for lbl, _, _ in course.waypoints()]
    mid_label = labels[1] if len(labels) == 3 else "경유지"
    provider, fallback_reason, geo = _route_provider(), None, None
    backoff_until = current_app.extensions.get("route_backoff_until", 0.0)
    if provider is not None and time.monotonic() < backoff_until:
        provider = None  # API failed recently: don't make every request wait for a timeout
        fallback_reason = "지도·경로 API에 연결할 수 없어 시뮬레이션 경로로 표시합니다 (잠시 후 다시 시도)."
    elif provider is not None:
        try:
            geo = provider.route(wps)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, route_guide.RouteError) as exc:
            fallback_reason = "지도·경로 API에 연결할 수 없어 시뮬레이션 경로로 표시합니다."
            if not isinstance(exc, route_guide.RouteError):  # network-level failure -> back off 60 s
                current_app.extensions["route_backoff_until"] = time.monotonic() + ROUTE_BACKOFF_S
            log.warning("Route API failed (%s): %s", type(exc).__name__, exc)
    elif fallback_reason is None:
        fallback_reason = "오프라인 모드: 시뮬레이션 경로로 표시합니다."
    try:
        if geo is not None:
            stops = [(mid_label if len(wps) == 3 else f"경유지 {k}", lat, lng) for k, (lat, lng) in enumerate(wps[1:-1], 1)]
            result = route_guide.build_profile(geo, limit, comfort, stops, powertrain)
        else:
            geo = route_guide.synthetic_route(course, build_segments(course, profile_of(vehicle)))
            end = course.checkpoint.label if course.checkpoint else "도착"
            result = route_guide.build_profile(geo, limit, comfort, (), powertrain, end_label=end)
    except route_guide.RouteError as exc:
        return jsonify(error=str(exc)), 400
    result.update(course=course.id, course_name=course.name, powertrain=powertrain,
                  fallback_reason=fallback_reason,
                  waypoints=[{"lat": lat, "lng": lng} for lat, lng in wps])
    resp = jsonify(result)
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp


# ---- Navigation-linked HUD (public rhythm mode + closed-circuit mode) -----------------
@bp.get("/drive")
def drive_page():
    mode = request.args.get("mode", "road")
    mode = mode if mode in ("road", "circuit") else "road"
    cid = request.args.get("course", "")
    course = catalog().courses_by_id.get(cid) or catalog().courses_by_id.get("jianjae") or catalog().courses[0]
    vehicle = active_vehicle()
    return render_template("drive.html", mode=mode, course=course, courses=catalog().courses, vehicle=vehicle,
                           tyres=corner_engine.TYRES, default_limit=LIMIT_BY_WIDTH[course.width],
                           cfg={"mode": mode, "course": course.id, "limit": LIMIT_BY_WIDTH[course.width],
                                "routeApi": url_for("main.route_guide_api"),
                                "circuitApi": url_for("main.circuit_guide_api")})


@bp.get("/api/circuit-guide")
def circuit_guide_api():
    """Closed-circuit performance profile for the active vehicle (fictional demo circuit)."""
    tyre = request.args.get("tyre", "street")
    if tyre not in corner_engine.TYRES:
        return jsonify(error="허용되지 않는 타이어 설정입니다."), 400
    mu = corner_engine.TYRES[tyre][1]
    v = active_vehicle()
    power_ps, weight, drive = (v["power_ps"], v["weight_kg"], v["drivetrain"]) if v else (380, 1900, "AWD")
    traction = {"AWD": 0.8, "FF": 0.55}.get(drive, 0.62) * mu * corner_engine.G
    dyn = corner_engine.Dynamics(a_lat=0.95 * mu * corner_engine.G, a_brake=0.9 * mu * corner_engine.G,
                                 a_traction=traction, power_w=power_ps * 735.5, mass_kg=weight)
    try:
        res = corner_engine.circuit_profile(dyn, route_guide.curvature)
    except ValueError:
        log.exception("Circuit layout failed")
        return jsonify(error="서킷 데이터를 계산하지 못했습니다."), 500
    res.update(tyre=tyre, tyre_label=corner_engine.TYRES[tyre][0],
               vehicle=(v["nickname"] if v else "기본 차량 (380 PS · 1,900 kg · AWD)"))
    resp = jsonify(res)
    resp.headers["Cache-Control"] = "private, max-age=300"
    return resp


# ---- HUD / learned navigation ----------------------------------------------------------
@bp.get("/hud")
def hud():
    cid = request.args.get("course", "")
    course = catalog().courses_by_id.get(cid)
    vehicle = active_vehicle()
    if course is None:
        ranked = rank_courses(catalog().courses, "sport", profile_of(vehicle), "clear", False, personalization())
        course = ranked[0].course
    segments = build_segments(course, profile_of(vehicle))
    if course.checkpoint is not None:
        stamped = course.id in stamps.collected_ids(get_db())
        segments.append({
            "idx": len(segments) + 1, "direction": "right", "severity": 0, "severity_label": "체크포인트",
            "distance_m": 0, "limit": segments[-1]["limit"] if segments else 50, "gradient": "flat",
            "speed": 0, "checkpoint": True,
            "message": f"{course.checkpoint.label} 도착 · 안전한 곳에 정차하세요",
            "hint": "스탬프 보유 중 · 재방문 기록" if stamped else f"스탬프 자동 적립 +{stamps.STAMP_POINTS}P",
        })
    return render_template("hud.html", course=course, courses=catalog().courses, segments=segments,
                           vehicle=vehicle)


# ---- ecosystem & competitors ------------------------------------------------------------
@bp.get("/ecosystem")
def ecosystem():
    return render_template("ecosystem.html")


@bp.get("/compare")
def compare():
    return render_template("compare.html")


# ---- Magma Performance Data Lab ---------------------------------------------------------
def _fleet_key() -> tuple:
    return tuple((p["model"], p["power_ps"], p["weight_kg"], p["transmission"]) for p in catalog().presets)


def _my_records() -> list[datalab.DriveRecord]:
    favs = [catalog().courses_by_id[c] for c in favorite_ids() if c in catalog().courses_by_id]
    common = Counter(t for c in favs for t in c.tags).most_common(1)
    tag = common[0][0] if common else "unknown"
    rows = get_db().execute(
        "SELECT r.result_json, v.model, v.transmission, v.power_ps, v.weight_kg FROM reports r "
        "JOIN vehicles v ON v.id = r.vehicle_id ORDER BY r.id").fetchall()
    out = []
    for r in rows:
        try:
            rec = datalab.record_from_report(json.loads(r["result_json"]), r["model"], r["transmission"],
                                             r["power_ps"], r["weight_kg"], tag)
        except (ValueError, TypeError, KeyError):
            log.warning("Skipping malformed report in Data Lab")
            continue
        if rec is not None:
            out.append(rec)
    return out


@bp.get("/datalab")
def datalab_page():
    consent = get_setting("data_consent") == "1"
    fleet = datalab.synthetic_fleet(_fleet_key())
    agg = datalab.aggregate(fleet)
    mine = _my_records() if consent else []
    return render_template("datalab.html", agg=agg, insights=datalab.insights(agg),
                           scatter=datalab.scatter_svg_points(agg.get("scatter", [])),
                           consent=consent, consent_at=get_setting("data_consent_at"),
                           mine=mine, mine_agg=datalab.aggregate(mine) if mine else None,
                           min_group=datalab.MIN_GROUP)


@bp.post("/datalab/consent")
def datalab_consent():
    on = request.form.get("consent") == "1"
    set_setting("data_consent", "1" if on else "0")
    set_setting("data_consent_at", date.today().isoformat() if on else "")
    log.info("Data sharing consent changed: %s", on)
    flash("데이터 제공에 동의했습니다. 언제든 철회할 수 있습니다." if on else "데이터 제공 동의를 철회했습니다.", "ok")
    return redirect(url_for("main.datalab_page"))


@bp.get("/datalab/export.json")
def datalab_export():
    source = request.args.get("source", "fleet")
    if source == "mine":
        if get_setting("data_consent") != "1":
            abort(403, description="데이터 제공 동의 후 내보낼 수 있습니다.")
        agg = datalab.aggregate(_my_records())
        label = "user-consented"
    else:
        agg = datalab.aggregate(datalab.synthetic_fleet(_fleet_key()))
        label = "synthetic-demo-fleet"
    agg = {k: v for k, v in agg.items() if k != "scatter"}  # aggregates only
    resp = jsonify(source=label, generated=date.today().isoformat(),
                   note="Aggregated, anonymised metrics only. No timestamps, traces or locations.",
                   min_group_size=datalab.MIN_GROUP, data=agg)
    resp.headers["Content-Disposition"] = f"attachment; filename=magma_datalab_{label}.json"
    return resp


@bp.get("/healthz")
def healthz():
    try:
        get_db().execute("SELECT 1").fetchone()
        return jsonify(status="ok")
    except sqlite3.Error:
        log.exception("Health check failed")
        return jsonify(status="error"), 503
