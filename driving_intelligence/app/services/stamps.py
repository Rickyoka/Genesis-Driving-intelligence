"""Drive-stamp tour and Genesis points (concept).

Visiting a course checkpoint stamps it automatically (geofence check-in). Stamps earn
points, and completing collections / milestones earns bonuses.

Design rules
------------
* Rewards depend only on *visiting*, never on speed, time or road risk.
* Every award has a unique ``ref`` in the ledger (DB UNIQUE), so no bonus can be paid twice,
  even under concurrent requests.
* Raw GPS coordinates are never stored: a check-in keeps only course, source and time.
* Browser GPS can be spoofed; production should verify with vehicle GPS (connected-car API).
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence

from .catalog import Course

EARTH_RADIUS_M = 6_371_008.8
STAMP_POINTS = 100
MAX_ACCURACY_M = 300          # reject fixes less precise than this
MAX_PLAUSIBLE_KMH = 250       # between two check-ins (anti-spoofing sanity check)
DUPLICATE_WINDOW_S = 600      # same course again within 10 min = same visit
SOURCES = ("gps", "vehicle", "demo")
SOURCE_LABELS = {"gps": "휴대폰 GPS", "vehicle": "차량 GPS", "demo": "시연"}


@dataclass(frozen=True)
class StampSet:
    key: str
    title: str
    course_ids: tuple[str, ...]
    bonus: int


SETS: tuple[StampSet, ...] = (
    StampSet("gangwon-passes", "강원 고갯길 컬렉션",
             ("hangyeryeong", "misiryeong-old", "daegwallyeong-old", "guryongnyeong", "manhangjae"), 500),
    StampSet("jirisan", "지리산·함양 와인딩", ("jianjae", "odojae", "jeongnyeongchi", "seongsamjae"), 500),
    StampSet("coastal", "해안 드라이브", ("donghae-7", "mulmi-coast", "dalmaji"), 300),
    StampSet("capital", "수도권·중부 근교", ("bugak-skyway", "yumyeongsan", "ihwaryeong-old"), 300),
    StampSet("night", "도심 야경", ("bugak-skyway", "dalmaji"), 200),
)
MILESTONES: tuple[tuple[int, str, int], ...] = ((3, "첫 3개 스탬프", 100), (8, "스탬프 8개", 400))
GRAND_TOUR_BONUS = 2000


class CheckinError(ValueError):
    """User-safe rejection message."""


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def validate_fix(lat: float, lng: float, accuracy: float) -> None:
    if not all(map(math.isfinite, (lat, lng, accuracy))):
        raise CheckinError("위치 값이 올바르지 않습니다.")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180) or accuracy < 0:
        raise CheckinError("위치 값이 올바르지 않습니다.")
    if accuracy > MAX_ACCURACY_M:
        raise CheckinError(f"위치 정확도가 낮습니다(±{accuracy:.0f} m). 하늘이 트인 곳에서 다시 시도하세요.")


def nearest(courses: Sequence[Course], lat: float, lng: float) -> tuple[Course | None, float]:
    best, best_d = None, math.inf
    for c in courses:
        if c.checkpoint is None:
            continue
        d = haversine_m(lat, lng, c.checkpoint.lat, c.checkpoint.lng)
        if d < best_d:
            best, best_d = c, d
    return best, best_d


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _award(conn: sqlite3.Connection, ref: str, amount: int, reason: str, source: str, at: str) -> bool:
    cur = conn.execute("INSERT OR IGNORE INTO points_ledger (ref, amount, reason, source, created_at) "
                       "VALUES (?,?,?,?,?)", (ref, amount, reason, source, at))
    return cur.rowcount == 1


def collected_ids(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT course_id FROM stamps")}


def record_visit(conn: sqlite3.Connection, course: Course, source: str, all_course_ids: Sequence[str],
                 now: str | None = None) -> dict:
    """Store a visit, stamp the course if new, and pay any newly earned bonuses (atomic)."""
    if source not in SOURCES:
        raise ValueError(f"unknown source {source}")
    at = now or _now()
    tag = " (시연)" if source == "demo" else ""
    awarded: list[dict] = []
    with conn:  # one transaction: visit + stamp + all awards, or nothing
        conn.execute("INSERT INTO visits (course_id, source, created_at) VALUES (?,?,?)",
                     (course.id, source, at))
        new_stamp = conn.execute(
            "INSERT OR IGNORE INTO stamps (course_id, source, first_at, last_at, visits) VALUES (?,?,?,?,1)",
            (course.id, source, at, at)).rowcount == 1
        if not new_stamp:
            conn.execute("UPDATE stamps SET visits = visits + 1, last_at = ? WHERE course_id = ?", (at, course.id))
        else:
            if _award(conn, f"stamp:{course.id}", STAMP_POINTS, f"{course.name} 스탬프{tag}", source, at):
                awarded.append({"reason": f"{course.name} 스탬프", "amount": STAMP_POINTS})
            have = collected_ids(conn)
            for s in SETS:
                if course.id in s.course_ids and set(s.course_ids) <= have:
                    if _award(conn, f"set:{s.key}", s.bonus, f"컬렉션 완성 · {s.title}{tag}", source, at):
                        awarded.append({"reason": f"컬렉션 완성 · {s.title}", "amount": s.bonus})
            for count, title, bonus in MILESTONES:
                if len(have) >= count and _award(conn, f"milestone:{count}", bonus, f"{title}{tag}", source, at):
                    awarded.append({"reason": title, "amount": bonus})
            if set(all_course_ids) <= have and _award(conn, "milestone:grand-tour", GRAND_TOUR_BONUS,
                                                      f"전국 그랜드 투어 완주{tag}", source, at):
                awarded.append({"reason": "전국 그랜드 투어 완주", "amount": GRAND_TOUR_BONUS})
    return {"course_id": course.id, "course": course.name, "new_stamp": new_stamp, "awarded": awarded,
            "balance": balance(conn), "stamps": len(collected_ids(conn)), "total": len(all_course_ids)}


def check_in(conn: sqlite3.Connection, courses: Sequence[Course], lat: float, lng: float, accuracy: float,
             source: str = "gps", now: datetime | None = None) -> dict:
    """Geofence check-in from a device/vehicle GPS fix. Returns a JSON-serialisable dict."""
    validate_fix(lat, lng, accuracy)
    course, dist = nearest(courses, lat, lng)
    if course is None or dist > course.checkpoint.radius_m:  # type: ignore[union-attr]
        return {"status": "outside", "nearest": course.name if course else None,
                "distance_km": round(dist / 1000, 1) if course else None}

    when = now or datetime.now()
    last = conn.execute("SELECT course_id, created_at FROM visits WHERE source != 'demo' "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    if last is not None:
        elapsed = max((when - datetime.fromisoformat(last[1])).total_seconds(), 1.0)
        if last[0] == course.id and elapsed < DUPLICATE_WINDOW_S:
            return {"status": "already", "course": course.name, "balance": balance(conn)}
        prev = next((c for c in courses if c.id == last[0]), None)
        if prev is not None and prev.id != course.id and prev.checkpoint and course.checkpoint:
            gap_m = haversine_m(prev.checkpoint.lat, prev.checkpoint.lng, course.checkpoint.lat, course.checkpoint.lng)
            gap_m = max(0.0, gap_m - prev.checkpoint.radius_m - course.checkpoint.radius_m)
            if gap_m / elapsed * 3.6 > MAX_PLAUSIBLE_KMH:
                raise CheckinError("직전 체크인 위치에서 이동한 속도가 비정상적이라 스탬프를 보류했습니다.")

    result = record_visit(conn, course, source, [c.id for c in courses],
                          now=when.isoformat(timespec="seconds"))
    result["status"] = "stamped" if result["new_stamp"] else "revisit"
    return result


def balance(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COALESCE(SUM(amount), 0) FROM points_ledger").fetchone()[0])


def ledger(conn: sqlite3.Connection, limit: int = 30) -> list[sqlite3.Row]:
    return conn.execute("SELECT ref, amount, reason, source, created_at FROM points_ledger "
                        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def set_progress(have: set[str], names: Mapping[str, str]) -> list[dict]:
    out = []
    for s in SETS:
        got = [cid for cid in s.course_ids if cid in have]
        out.append({"key": s.key, "title": s.title, "bonus": s.bonus, "got": len(got),
                    "need": len(s.course_ids), "done": len(got) == len(s.course_ids),
                    "courses": [{"id": cid, "name": names.get(cid, cid), "have": cid in have}
                                for cid in s.course_ids]})
    return out


def reset_all(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute("DELETE FROM visits")
        conn.execute("DELETE FROM stamps")
        conn.execute("DELETE FROM points_ledger")
