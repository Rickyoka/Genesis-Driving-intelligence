"""Corner phases, public-road rhythm sections, and closed-circuit performance profile.

Public roads (navigation-linked HUD, "rhythm mode")
    Fun comes from *flow*, not speed: brake on the straight, hold a steady speed through the
    corner, roll back on smoothly from the exit — all within the comfort-limited advisory profile
    produced by route_guide. "Rhythm sections" are clusters of linked corners where that flow
    is most enjoyable. Nothing here raises a speed.

Closed circuit ("circuit mode")
    Performance coaching — braking points, turn-in, apex, full throttle — computed from tyre grip,
    braking capacity and the vehicle's power-to-weight. Only for a closed course: the UI requires
    explicit confirmation, and in a production build it would be enabled only inside a registered
    circuit geofence. The demo circuit is FICTIONAL (no real track data).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

G = 9.80665

# ----------------------------------------------------------------------------- shared geometry
def resample_xy(xy: Sequence[tuple[float, float]], step: float) -> list[tuple[float, float]]:
    out = [xy[0]]
    carry = 0.0
    for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
        seg = math.hypot(x2 - x1, y2 - y1)
        if seg == 0:
            continue
        d = step - carry
        while d <= seg:
            t = d / seg
            out.append((x1 + (x2 - x1) * t, y1 + (y2 - y1) * t))
            d += step
        carry = seg - (d - step)
    return out


def signed_curvature(curv: Sequence[tuple[float, int]]) -> list[float]:
    """(radius, dir) -> signed 1/R (+ right, - left); 0 for straight."""
    return [0.0 if not math.isfinite(r) else d / r for r, d in curv]


# ----------------------------------------------------------------------------- corner detection
def detect_corners(curv: Sequence[tuple[float, int]], v: Sequence[float], ds: float,
                   r_thr: float = 250.0) -> list[dict]:
    """Group samples with radius < r_thr into corners and locate their phases on the speed profile.

    brake_m    : where deceleration for this corner begins (None if no slowing is needed)
    turnin_m   : geometric start of the bend
    apex_m     : slowest point inside the bend
    throttle_m : first point after the apex where speed rises again
    exit_m     : geometric end of the bend
    """
    n = len(v)
    flags = [math.isfinite(r) and r < r_thr for r, _ in curv]
    groups: list[list[int]] = []
    i = 0
    while i < n:
        if flags[i]:
            j = i
            # extend while still bending the same way (an S-bend splits into two corners)
            while j + 1 < n and flags[j + 1] and curv[j + 1][1] == curv[i][1]:
                j += 1
            groups.append([i, j])
            i = j + 1
        else:
            i += 1
    merged: list[list[int]] = []
    for g in groups:  # bridge tiny gaps inside the same bend
        if merged and g[0] - merged[-1][1] <= 3 and _dir(curv, g) == _dir(curv, merged[-1]):
            merged[-1][1] = g[1]
        else:
            merged.append(g)
    corners = []
    for a, b in merged:
        rmin = min(curv[k][0] for k in range(a, b + 1))
        if b - a < 1 and rmin > 60:
            continue  # digitisation noise, not a real bend
        apex = min(range(a, b + 1), key=lambda k: (v[k], curv[k][0]))
        brake = apex
        while brake > a and v[brake - 1] <= v[brake] + 0.02:   # walk back over constant-speed arc
            brake -= 1
        while brake > 0 and v[brake - 1] > v[brake] + 0.02:    # then over the deceleration zone
            brake -= 1
        if v[brake] <= v[apex] + 0.05:                          # never slowed: no braking point
            brake = apex
        thr = b                    # default: no distinct throttle point -> the bend's exit
        for k in range(apex, min(n - 1, b + 10)):
            if v[k + 1] > v[k] + 0.02:      # speed starts rising: throttle point
                thr = k
                break
            if v[k + 1] < v[k] - 0.02:      # already braking for the next bend
                break
        corners.append({
            "dir": _dir(curv, [a, b]),
            "radius_m": round(rmin),
            "brake_m": round(brake * ds) if brake < apex else None,
            "turnin_m": round(a * ds),
            "apex_m": round(apex * ds),
            "throttle_m": round(thr * ds),
            "exit_m": round(b * ds),
            "entry_kmh": round(v[brake] * 3.6),
            "min_kmh": round(v[apex] * 3.6),
        })
    return corners


def _dir(curv, g) -> int:
    s = sum(curv[k][1] for k in range(g[0], g[1] + 1))
    return 1 if s >= 0 else -1


def severity(radius_m: float) -> str:
    if radius_m < 45:
        return "헤어핀"
    if radius_m < 90:
        return "급커브"
    if radius_m < 180:
        return "보통"
    return "완만"


def rhythm_sections(corners: Sequence[dict], limit_kmh: float, max_gap_m: float = 250.0,
                    min_count: int = 3) -> list[dict]:
    """Clusters of linked corners: the most enjoyable 'flow' stretches on a public road."""
    out, cur = [], []
    for c in corners:
        if cur and c["turnin_m"] - cur[-1]["exit_m"] > max_gap_m:
            if len(cur) >= min_count:
                out.append(_section(cur, limit_kmh))
            cur = []
        cur.append(c)
    if len(cur) >= min_count:
        out.append(_section(cur, limit_kmh))
    return out


def _section(cs: Sequence[dict], limit_kmh: float) -> dict:
    start = cs[0]["brake_m"] if cs[0]["brake_m"] is not None else cs[0]["turnin_m"]
    lo = min(c["min_kmh"] for c in cs)
    hi = max(c["entry_kmh"] for c in cs)
    return {"from_m": start, "to_m": cs[-1]["exit_m"], "count": len(cs),
            "speed_band": [lo, min(hi, round(limit_kmh))],
            "title": f"리듬 구간 · 연속 코너 {len(cs)}개",
            "tip": "감속은 직선에서 끝내고, 코너 중엔 일정하게, 출구부터 부드럽게 — 속도보다 흐름이 재미입니다"}


# ----------------------------------------------------------------------------- circuit mode
TYRES = {"street": ("스트리트 타이어", 0.95), "semi": ("세미 슬릭", 1.15)}
CIRCUIT_DS = 5.0

# Fictional demo layout: (kind, length_m) for straights, (kind, radius_m, degrees, name) for bends.
# Net heading change = +360 deg (clockwise loop); the small closure error is spread over the lap.
DEMO_CIRCUIT = {
    "name": "DI 데모 서킷 (가상)",
    "segments": [
        ("S", 650),                                   # main straight (north)
        ("R", 45, 90, "T1"), ("S", 220),
        ("L", 80, 45, "T2"), ("R", 80, 45, "T3"),     # esses
        ("S", 180), ("R", 140, 90, "T4 스위퍼"),
        ("S", 600), ("R", 28, 180, "T5 헤어핀"),
        ("S", 160), ("L", 60, 90, "T6"),
        ("S", 260), ("R", 60, 30, "T7"), ("L", 60, 30, "T8"),   # chicane
        ("S", 200), ("R", 50, 90, "T9"), ("S", 150),
    ],
}


@dataclass(frozen=True)
class Dynamics:
    a_lat: float        # m/s^2 cornering limit (with margin)
    a_brake: float      # m/s^2
    a_traction: float   # m/s^2 grip-limited acceleration
    power_w: float
    mass_kg: float
    cda: float = 0.75   # drag area m^2

    def accel(self, v: float) -> float:
        drive = min(self.a_traction, 0.85 * self.power_w / (self.mass_kg * max(v, 4.0)))
        drag = 0.5 * 1.2 * self.cda * v * v / self.mass_kg
        return drive - drag - 0.012 * G


def _trace(segments) -> tuple[list[tuple[float, float]], list[dict], list[float]]:
    """Walk the segment list; return points, corner names and the heading of each straight."""
    x = y = h = 0.0
    pts = [(0.0, 0.0)]
    names, headings = [], []
    dist = 0.0
    for seg in segments:
        if seg[0] == "S":
            headings.append(h)
            steps = max(1, int(seg[1] // 5))
            for _ in range(steps):
                x += seg[1] / steps * math.sin(h); y += seg[1] / steps * math.cos(h); pts.append((x, y))
            dist += seg[1]
        else:
            kind, r, deg, name = seg
            ang = math.radians(deg) * (1 if kind == "R" else -1)
            arc = r * abs(ang)
            steps = max(6, int(arc // 2))
            names.append({"name": name, "at_m": dist + arc / 2})
            for _ in range(steps):
                h += ang / steps
                x += arc / steps * math.sin(h); y += arc / steps * math.cos(h); pts.append((x, y))
            dist += arc
    return pts, names, headings


def build_layout(spec: dict = DEMO_CIRCUIT) -> tuple[list[tuple[float, float]], list[dict]]:
    """Close the loop exactly by lengthening/shortening two straights (2x2 linear solve).
    Bends are never distorted, so radii stay exactly as specified."""
    segs = [list(s) for s in spec["segments"]]
    pts, _, heads = _trace(segs)
    ex, ey = pts[-1]
    s_idx = [k for k, sg in enumerate(segs) if sg[0] == "S"]
    best = None
    for a in range(len(s_idx)):
        for b in range(a + 1, len(s_idx)):
            ha, hb = heads[a], heads[b]
            det = math.sin(ha) * math.cos(hb) - math.sin(hb) * math.cos(ha)
            if abs(det) < 0.3:
                continue
            da = (-ex * math.cos(hb) + ey * math.sin(hb)) / det
            db = (-ey * math.sin(ha) + ex * math.cos(ha)) / det
            la, lb = segs[s_idx[a]][1] + da, segs[s_idx[b]][1] + db
            if la >= 120 and lb >= 120:
                score = abs(da) + abs(db)
                if best is None or score < best[0]:
                    best = (score, s_idx[a], la, s_idx[b], lb)
    if best is None:
        raise ValueError("circuit layout cannot be closed")
    segs[best[1]][1], segs[best[3]][1] = best[2], best[4]
    pts, names, _ = _trace(segs)
    return pts, names


def circuit_profile(dyn: Dynamics, curvature_fn, spec: dict = DEMO_CIRCUIT) -> dict:
    raw, names = build_layout(spec)
    xy = resample_xy(raw, CIRCUIT_DS)
    n = len(xy)
    curv = curvature_fn(xy)
    vtop = 300 / 3.6
    cap = [min(vtop, math.sqrt(dyn.a_lat * r)) if math.isfinite(r) else vtop for r, _ in curv]
    # three laps back-to-back so the lap is periodic (flying lap), keep the middle one
    c3 = cap * 3
    v = c3[:]
    for i in range(len(v) - 2, -1, -1):
        v[i] = min(v[i], math.sqrt(v[i + 1] ** 2 + 2 * dyn.a_brake * CIRCUIT_DS))
    for i in range(1, len(v)):
        v[i] = min(v[i], math.sqrt(max(0.0, v[i - 1] ** 2 + 2 * dyn.accel(v[i - 1]) * CIRCUIT_DS)))
    v = v[n:2 * n]
    corners = detect_corners(curv, v, CIRCUIT_DS, r_thr=200.0)
    for c in corners:  # attach layout names by proximity
        near = min(names, key=lambda nm: abs(nm["at_m"] - c["apex_m"]))
        c["name"] = near["name"]
        c["severity"] = severity(c["radius_m"])
    lap = sum(CIRCUIT_DS / max(s, 1.0) for s in v)
    step = max(1, n // 500)
    xs, ys = [p[0] for p in xy], [p[1] for p in xy]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    span = max(maxx - minx, maxy - miny) or 1.0
    norm = [[round((p[0] - minx) / span, 4), round(1 - (p[1] - miny) / span, 4)] for p in xy[::step]]
    sc = signed_curvature(curv)
    total = (n - 1) * CIRCUIT_DS
    return {
        "name": spec["name"],
        "length_m": round(total),
        "lap_time_s": round(lap, 1),
        "vmax_kmh": round(max(v) * 3.6),
        "vmin_kmh": round(min(v) * 3.6),
        "profile": [[round(i * CIRCUIT_DS), round(v[i] * 3.6, 1)] for i in range(0, n, step)],
        "curv": [round(sc[i], 5) for i in range(0, n, step)],
        "map": norm,
        "corners": corners,
        "cues": circuit_cues(corners, total),
        "params": {"a_lat_g": round(dyn.a_lat / G, 2), "a_brake_g": round(dyn.a_brake / G, 2)},
    }


def circuit_cues(corners: Sequence[dict], lap_m: float) -> list[dict]:
    cues = []
    for c in corners:
        nm = c.get("name", "")
        if c["brake_m"] is not None:
            for board in (150, 100, 50):
                at = c["brake_m"] - board
                if at > 0 and (not cues or at > cues[-1]["at_m"]):
                    cues.append({"at_m": at, "kind": "board", "title": f"{board}", "detail": f"{nm} 브레이킹까지 {board} m"})
            cues.append({"at_m": c["brake_m"], "kind": "brake", "title": "BRAKE",
                         "detail": f"{nm} · {c['entry_kmh']} → {c['min_kmh']} km/h 강하게, 직선에서"})
        cues.append({"at_m": c["turnin_m"], "kind": "turnin", "title": "TURN IN",
                     "detail": f"{nm} · {('우' if c['dir'] > 0 else '좌')} {c['severity']} (R {c['radius_m']} m)"})
        cues.append({"at_m": c["apex_m"], "kind": "apex", "title": "APEX", "detail": f"{nm} · 최저 {c['min_kmh']} km/h"})
        cues.append({"at_m": c["throttle_m"], "kind": "throttle", "title": "THROTTLE", "detail": f"{nm} · 출구로 점진적 가속"})
        cues.append({"at_m": c["exit_m"], "kind": "full", "title": "FULL", "detail": f"{nm} 탈출 · 스티어링 풀고 풀 스로틀"})
    cues.sort(key=lambda q: q["at_m"])
    return [q for q in cues if 0 <= q["at_m"] <= lap_m]
