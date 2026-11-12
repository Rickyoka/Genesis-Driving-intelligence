"""HUD / navigation-module simulation.

The prototype has no real road geometry, so this builds a *deterministic,
plausible* sequence of corners from a course's attributes purely to demonstrate
the head-up display UX. The page labels it as a simulation. In production the
segments come from the map API's road geometry (curvature per link).
"""
from __future__ import annotations

import random
import zlib

from .catalog import Course
from .scoring import VehicleProfile

SEVERITY = ("완만", "보통", "급커브", "헤어핀")
LIMIT_BY_WIDTH = {"narrow": 50, "medium": 60, "wide": 70}


def _downhill_hint(vehicle: VehicleProfile | None) -> str:
    if vehicle is None:
        return "저단 기어·엔진 브레이크 활용"
    if vehicle.transmission == "EV":
        return "회생제동 강도 높임"
    if vehicle.transmission == "MT":
        return "3단 이하 · 엔진 브레이크"
    return "수동 모드 · 저단 고정"


def build_segments(course: Course, vehicle: VehicleProfile | None, max_segments: int = 12) -> list[dict]:
    rng = random.Random(zlib.crc32(course.id.encode("utf-8")))  # same course -> same simulation
    n = max(5, min(max_segments, int(round(course.length_km))))
    limit = LIMIT_BY_WIDTH[course.width]
    hairpin_p = course.hairpin / 3 * 0.45
    sharp_p = (course.curve_level - 1) / 4 * 0.5
    segments: list[dict] = []
    for i in range(n):
        r = rng.random()
        if r < hairpin_p:
            sev = 3
        elif r < hairpin_p + sharp_p:
            sev = 2
        elif r < hairpin_p + sharp_p + 0.3:
            sev = 1
        else:
            sev = 0
        downhill = course.elevation_m >= 300 and i >= n // 2
        uphill = course.elevation_m >= 300 and i < n // 2
        distance = rng.choice((150, 200, 250, 300, 400, 500, 600)) if sev < 3 else rng.choice((100, 150, 200))
        direction = rng.choice(("left", "right"))
        if sev >= 2:
            message = f"{SEVERITY[sev]} {distance} m 앞 · 직선에서 감속 완료"
        elif sev == 1:
            message = f"{'좌' if direction == 'left' else '우'}커브 · 차로 중앙 유지"
        else:
            message = "완만한 구간 · 일정한 속도 유지"
        # never pair an "accelerate" hint with a corner that needs braking
        hint = _downhill_hint(vehicle) if downhill else ("오르막 · 일정한 속도 유지" if uphill and sev <= 1 else "")
        segments.append({
            "idx": i + 1,
            "direction": direction,
            "severity": sev,
            "severity_label": SEVERITY[sev],
            "distance_m": distance,
            "limit": limit,
            "gradient": "down" if downhill else ("up" if uphill else "flat"),
            "message": message,
            "hint": hint,
            # displayed speed always stays below the (sample) limit and slows for tighter corners
            "speed": max(20, limit - (5, 10, 20, 30)[sev] - rng.randint(0, 4)),
        })
    return segments
