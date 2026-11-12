"""Route guide: where to slow down, where to stop, where to (gently) speed back up.

Pipeline
--------
1. Route geometry from a map/routing API (OSRM by default; pluggable), cached on disk.
   If the API is unreachable, a clearly-labelled synthetic route is generated so demos still work.
2. Resample the polyline every 10 m and estimate road curvature (3-point circumradius).
3. Advisory speed per point = min(speed limit, sqrt(a_lat_comfort x R)), plus caps at
   turns / junctions (OSRM maneuvers) and full stops at start, checkpoint and destination.
4. Backward pass with a comfortable deceleration, forward pass with gentle acceleration
   (classic speed-profile planning) -> a physically consistent advisory profile.
5. Split into zones: 감속(decel) / 정속(cruise) / 가속(accel, only back up to the limit) / 정지(stop).

Safety framing
--------------
* Lateral comfort limit is 0.15–0.20 g – far below any vehicle's grip limit; it mirrors road
  design comfort values and navigation-based curve slowdown in driver-assist systems.
  It is an *advisory ceiling*, never a target to reach. Road signs and conditions always win.
* "Acceleration" zones only ever recover speed up to the posted limit. No setting raises the
  comfort limit; performance coaching remains circuit-only.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from . import corner_engine
from .catalog import Course, in_korea

log = logging.getLogger(__name__)

G = 9.80665
DS = 10.0                      # resample step (m)
CURV_SPAN = 3                  # points each side for curvature (=> 60 m chord)
DECEL = 1.5                    # m/s^2 comfortable braking (~0.15 g)
ACCEL = 1.0                    # m/s^2 gentle acceleration (~0.1 g)
COMFORT_G = {"relaxed": 0.15, "standard": 0.20}
TURN_CAP_KMH = {"turn": 20.0, "roundabout": 25.0, "rotary": 25.0, "fork": 40.0, "merge": 40.0,
                "on ramp": 40.0, "off ramp": 40.0, "end of road": 0.0}
MIN_ZONE_M = 40.0
MAX_WAYPOINTS = 6
OSRM_URL = "https://router.project-osrm.org/route/v1/driving/{coords}?overview=full&geometries=geojson&steps=true"
USER_AGENT = "DrivingIntelligence-Prototype/0.4 (concept demo)"

ZONE_LABELS = {"decel": "감속", "cruise": "정속", "accel": "가속 (제한속도까지)", "stop": "정지"}


class RouteError(ValueError):
    """User-safe error (bad waypoints etc.)."""


@dataclass
class RouteGeometry:
    points: list[tuple[float, float]]                 # (lat, lng)
    maneuvers: list[dict] = field(default_factory=list)  # {"lat","lng","type","modifier","name"}
    source: str = "osrm"                               # "osrm" | "synthetic"
    distance_m: float | None = None
    duration_s: float | None = None


# =========================================================================== providers
def validate_waypoints(wps: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    if not 2 <= len(wps) <= MAX_WAYPOINTS:
        raise RouteError(f"경유 지점은 2~{MAX_WAYPOINTS}개여야 합니다.")
    out = []
    for lat, lng in wps:
        if not (math.isfinite(lat) and math.isfinite(lng)) or not in_korea(lat, lng):
            raise RouteError("국내 범위 밖의 좌표가 포함되어 있습니다.")
        out.append((round(lat, 6), round(lng, 6)))
    return out


def parse_waypoint_param(raw: str) -> list[tuple[float, float]]:
    """'lat,lng;lat,lng;...' -> list. Raises RouteError on malformed input."""
    pairs = []
    for part in raw.split(";")[: MAX_WAYPOINTS + 1]:
        try:
            a, b = part.split(",")
            pairs.append((float(a), float(b)))
        except ValueError:
            raise RouteError("경유 지점 형식이 올바르지 않습니다 (lat,lng;lat,lng).") from None
    return validate_waypoints(pairs)


def parse_osrm(doc: dict) -> RouteGeometry:
    if doc.get("code") != "Ok" or not doc.get("routes"):
        raise RouteError(f"경로를 찾지 못했습니다 ({doc.get('code', 'unknown')}).")
    route = doc["routes"][0]
    coords = route["geometry"]["coordinates"]
    points = [(float(lat), float(lng)) for lng, lat in coords]
    maneuvers = []
    for leg in route.get("legs", []):
        for step in leg.get("steps", []):
            m = step.get("maneuver", {})
            loc = m.get("location")
            if not loc:
                continue
            maneuvers.append({"lat": float(loc[1]), "lng": float(loc[0]), "type": str(m.get("type", "")),
                              "modifier": str(m.get("modifier", "")), "name": str(step.get("name", ""))})
    if len(points) < 2:
        raise RouteError("경로 형상이 비어 있습니다.")
    return RouteGeometry(points=points, maneuvers=maneuvers, source="osrm",
                         distance_m=float(route.get("distance", 0)) or None,
                         duration_s=float(route.get("duration", 0)) or None)


class OsrmProvider:
    """OSRM HTTP API. The public demo server is for light prototype use only —
    use a self-hosted OSRM or a commercial Korean routing API in production."""

    def __init__(self, cache_dir: Path, url_template: str = OSRM_URL, timeout: float = 8.0):
        self.cache_dir = cache_dir
        self.url_template = url_template
        self.timeout = timeout
        cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, wps: Sequence[tuple[float, float]]) -> Path:
        key = hashlib.sha256(json.dumps(wps).encode()).hexdigest()[:24]
        return self.cache_dir / f"osrm_{key}.json"

    def route(self, wps: Sequence[tuple[float, float]]) -> RouteGeometry:
        wps = validate_waypoints(wps)
        path = self._cache_path(wps)
        if path.exists():
            try:
                return parse_osrm(json.loads(path.read_text(encoding="utf-8")))
            except (ValueError, KeyError, RouteError):
                log.warning("Discarding corrupt route cache %s", path.name)
                path.unlink(missing_ok=True)
        coords = ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in wps)  # validated floats only
        req = urllib.request.Request(self.url_template.format(coords=coords),
                                     headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (fixed https host)
            if resp.status != 200:
                raise RouteError(f"경로 API 오류 (HTTP {resp.status})")
            raw = resp.read(5_000_000)
        doc = json.loads(raw.decode("utf-8"))
        geo = parse_osrm(doc)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        tmp.replace(path)  # atomic
        return geo


def synthetic_route(course: Course, segments: Sequence[dict]) -> RouteGeometry:
    """Offline fallback: plausible road shape from the course's HUD segments, anchored so the
    route ends at the checkpoint. NOT real geometry — labelled as such in the UI."""
    radius = [400.0, 150.0, 70.0, 35.0]
    turn = [20.0, 45.0, 90.0, 150.0]
    x = y = 0.0
    heading = 0.0
    pts = [(x, y)]
    for s in segments:
        straight = max(150.0, s["distance_m"] * 2)
        for _ in range(int(straight // 20)):
            x += 20 * math.sin(heading); y += 20 * math.cos(heading); pts.append((x, y))
        sign = -1.0 if s["direction"] == "left" else 1.0
        r, ang = radius[s["severity"]], math.radians(turn[s["severity"]])
        steps = max(3, int(r * ang / 10))
        for _ in range(steps):
            heading += sign * ang / steps
            x += (r * ang / steps) * math.sin(heading); y += (r * ang / steps) * math.cos(heading)
            pts.append((x, y))
    for _ in range(15):  # final straight into the checkpoint
        x += 20 * math.sin(heading); y += 20 * math.cos(heading); pts.append((x, y))
    cp = course.checkpoint
    lat0, lng0 = (cp.lat, cp.lng) if cp else (37.5, 127.0)
    ex, ey = pts[-1]
    m_per_deg_lat = 111_320.0
    m_per_deg_lng = 111_320.0 * math.cos(math.radians(lat0))
    points = [(lat0 + (py - ey) / m_per_deg_lat, lng0 + (px - ex) / m_per_deg_lng) for px, py in pts]
    return RouteGeometry(points=points, maneuvers=[], source="synthetic")


# =========================================================================== geometry helpers
def _to_xy(points: Sequence[tuple[float, float]]) -> tuple[list[tuple[float, float]], float, float]:
    lat0 = sum(p[0] for p in points) / len(points)
    kx = 111_320.0 * math.cos(math.radians(lat0))
    ky = 111_320.0
    lng0 = points[0][1]
    lat_ref = points[0][0]
    return [((lng - lng0) * kx, (lat - lat_ref) * ky) for lat, lng in points], kx, ky


def resample(points: Sequence[tuple[float, float]], step: float = DS) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Return (xy samples, latlng samples) every `step` metres along the polyline."""
    xy, kx, ky = _to_xy(points)
    lng0, lat_ref = points[0][1], points[0][0]
    out_xy = [xy[0]]
    carry = 0.0
    for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
        seg = math.hypot(x2 - x1, y2 - y1)
        if seg == 0:
            continue
        d = step - carry
        while d <= seg:
            t = d / seg
            out_xy.append((x1 + (x2 - x1) * t, y1 + (y2 - y1) * t))
            d += step
        carry = seg - (d - step)
    if out_xy[-1] != xy[-1]:
        out_xy.append(xy[-1])
    out_ll = [(lat_ref + y / ky, lng0 + x / kx) for x, y in out_xy]
    return out_xy, out_ll


def curvature(xy: Sequence[tuple[float, float]], span: int = CURV_SPAN) -> list[tuple[float, int]]:
    """(radius_m, direction) per sample; direction -1 = left, +1 = right, 0 = straight."""
    n = len(xy)
    out: list[tuple[float, int]] = []
    for i in range(n):
        a, b = max(0, i - span), min(n - 1, i + span)
        if b - a < 2:
            out.append((math.inf, 0)); continue
        (x1, y1), (x2, y2), (x3, y3) = xy[a], xy[i], xy[b]
        cross = (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)
        la, lb, lc = math.dist(xy[a], xy[i]), math.dist(xy[i], xy[b]), math.dist(xy[a], xy[b])
        area2 = abs(cross)
        if area2 < 1e-6 or la == 0 or lb == 0:
            out.append((math.inf, 0)); continue
        r = la * lb * lc / (2 * area2)
        out.append((r, -1 if cross > 0 else 1))
    # light smoothing of 1/R to suppress digitisation noise
    inv = [0.0 if math.isinf(r) else 1.0 / r for r, _ in out]
    sm = [sum(inv[max(0, i - 2): i + 3]) / len(inv[max(0, i - 2): i + 3]) for i in range(n)]
    return [(math.inf if v < 1e-5 else 1.0 / v, out[i][1]) for i, v in enumerate(sm)]


def _nearest_index(ll: Sequence[tuple[float, float]], lat: float, lng: float) -> int:
    k = math.cos(math.radians(lat))
    return min(range(len(ll)), key=lambda i: (ll[i][0] - lat) ** 2 + ((ll[i][1] - lng) * k) ** 2)


# =========================================================================== speed profile
@dataclass
class Guidance:
    kind: str           # decel | accel | stop | cruise
    at_m: float
    lat: float
    lng: float
    title: str
    detail: str
    target_kmh: float | None = None


def build_profile(geo: RouteGeometry, limit_kmh: float, comfort: str = "standard",
                  stop_points: Iterable[tuple[str, float, float]] = (), powertrain: str = "ice",
                  end_label: str = "도착") -> dict:
    if comfort not in COMFORT_G:
        raise RouteError("허용되지 않는 편안함 설정입니다.")
    if not 20 <= limit_kmh <= 110:
        raise RouteError("제한속도 값이 올바르지 않습니다.")
    xy, ll = resample(geo.points)
    n = len(ll)
    if n < 3:
        raise RouteError("경로가 너무 짧습니다.")
    a_lat = COMFORT_G[comfort] * G
    vmax = limit_kmh / 3.6
    curv = curvature(xy)

    cap = [min(vmax, math.sqrt(a_lat * r)) if math.isfinite(r) else vmax for r, _ in curv]
    reason: list[str | None] = [None] * n
    for i, (r, d) in enumerate(curv):
        if math.isfinite(r) and math.sqrt(a_lat * r) < vmax:
            reason[i] = f"반경 약 {r:.0f} m {'좌' if d < 0 else '우'}커브"

    for m in geo.maneuvers:
        t = m["type"]
        if t in ("depart", "arrive"):
            continue
        key = t if t in TURN_CAP_KMH else ("turn" if t in ("new name", "continue") and m["modifier"] in
                                             ("left", "right", "sharp left", "sharp right") else None)
        if key is None:
            continue
        i = _nearest_index(ll, m["lat"], m["lng"])
        v = TURN_CAP_KMH[key] / 3.6
        if v < cap[i]:
            cap[i] = v
            side = {"left": "좌", "right": "우", "sharp left": "급좌", "sharp right": "급우",
                    "slight left": "좌측", "slight right": "우측", "uturn": "유턴"}.get(m["modifier"], "")
            label = {"end of road": "T자 교차로 일시정지", "roundabout": "회전교차로", "rotary": "회전교차로",
                     "fork": "갈림길", "merge": "합류", "on ramp": "진입로", "off ramp": "진출로"}.get(t, f"{side}회전 교차로")
            reason[i] = label + (f" ({m['name']})" if m["name"] else "")

    stops = [("출발", ll[0][0], ll[0][1])] + list(stop_points) + [(end_label, ll[-1][0], ll[-1][1])]
    stop_idx: dict[int, str] = {}
    for label, lat, lng in stops:
        i = _nearest_index(ll, lat, lng)
        cap[i] = 0.0
        stop_idx[i] = label
        reason[i] = label

    v = cap[:]
    for i in range(n - 2, -1, -1):          # backward: can we slow down in time?
        v[i] = min(v[i], math.sqrt(v[i + 1] ** 2 + 2 * DECEL * DS))
    for i in range(1, n):                   # forward: gentle acceleration only
        v[i] = min(v[i], math.sqrt(v[i - 1] ** 2 + 2 * ACCEL * DS))

    kinds = []
    for i in range(n):
        if v[i] < 0.3:
            kinds.append("stop")
            continue
        nxt = v[min(i + 1, n - 1)]
        if nxt < v[i] - 0.05:
            kinds.append("decel")
        elif nxt > v[i] + 0.05:
            kinds.append("accel")
        else:
            kinds.append("cruise")

    corners = corner_engine.detect_corners(curv, v, DS, r_thr=250.0)
    for c in corners:
        c["severity"] = corner_engine.severity(c["radius_m"])
    rhythm = corner_engine.rhythm_sections(corners, limit_kmh)
    signed = corner_engine.signed_curvature(curv)
    zones = _merge_zones(kinds, v)
    guidance = _guidance(zones, v, ll, reason, stop_idx, vmax, powertrain)
    total = (n - 1) * DS
    step = max(1, n // 400)
    zone_out = [{"kind": z["kind"], "label": ZONE_LABELS[z["kind"]], "from_m": round(z["a"] * DS),
                 "to_m": round(z["b"] * DS),
                 "coords": [[round(ll[j][0], 6), round(ll[j][1], 6)] for j in range(z["a"], z["b"] + 1, max(1, (z["b"] - z["a"]) // 60 or 1))]
                           + [[round(ll[z["b"]][0], 6), round(ll[z["b"]][1], 6)]]}
                for z in zones]
    shares = {k: round(100 * sum((z["b"] - z["a"]) for z in zones if z["kind"] == k) / max(1, n - 1)) for k in ZONE_LABELS}
    return {
        "source": geo.source,
        "distance_m": round(total),
        "limit_kmh": limit_kmh,
        "comfort": comfort,
        "comfort_g": COMFORT_G[comfort],
        "zones": zone_out,
        "guidance": [g.__dict__ for g in guidance],
        "profile": [[round(i * DS), round(v[i] * 3.6, 1)] for i in range(0, n, step)] + [[round(total), round(v[-1] * 3.6, 1)]],
        "path": [[round(lat, 6), round(lng, 6)] for lat, lng in ll[::step]] + [[round(ll[-1][0], 6), round(ll[-1][1], 6)]],
        "shares": shares,
        "est_minutes": round(sum(DS / max(v[i], 1.0) for i in range(n)) / 60, 1),
        "curv": [round(signed[i], 5) for i in range(0, n, step)] + [round(signed[-1], 5)],
        "corners": corners,
        "rhythm": rhythm,
    }


def _merge_zones(kinds: list[str], v: list[float]) -> list[dict]:
    zones: list[dict] = []
    for i, k in enumerate(kinds):
        if zones and zones[-1]["kind"] == k:
            zones[-1]["b"] = i
        else:
            zones.append({"kind": k, "a": i, "b": i})
    # absorb tiny non-stop zones into the previous zone (noise from curvature jitter)
    merged: list[dict] = []
    for z in zones:
        if merged and z["kind"] != "stop" and (z["b"] - z["a"]) * DS < MIN_ZONE_M:
            merged[-1]["b"] = z["b"]
        elif merged and merged[-1]["kind"] == z["kind"]:
            merged[-1]["b"] = z["b"]
        else:
            merged.append(dict(z))
    return merged


def _guidance(zones: list[dict], v: list[float], ll, reason, stop_idx, vmax: float, powertrain: str) -> list[Guidance]:
    out: list[Guidance] = []
    prev_kind = None
    how = "회생제동 단계를 올려 부드럽게" if powertrain == "ev" else "가속 페달에서 발을 떼고 엔진 브레이크로"
    for z in zones:
        a, b = z["a"], z["b"]
        if z["kind"] == "decel":
            j = min(range(a, min(b + 2, len(v))), key=lambda k: v[k])
            why = next((reason[k] for k in range(j, min(j + 4, len(v))) if reason[k]), None) or reason[j] or "다음 구간"
            tgt = round(v[j] * 3.6)
            out.append(Guidance("decel", a * DS, ll[a][0], ll[a][1],
                                f"감속 시작 → {tgt} km/h 이하" if tgt > 0 else "감속 시작 → 정지 준비",
                                f"{why} · {(j - a) * DS:.0f} m 동안 {how} 감속", tgt))
        elif z["kind"] == "accel":
            top = round(v[b] * 3.6)
            where = "정차 후 출발 구간" if prev_kind == "stop" else "코너·교차로를 벗어난 구간"
            out.append(Guidance("accel", a * DS, ll[a][0], ll[a][1],
                                f"가속 가능 → {top} km/h까지",
                                f"{where} · 제한속도 {round(vmax * 3.6)} km/h 이내에서 {(b - a) * DS:.0f} m 동안 부드럽게 회복", top))
        elif z["kind"] == "stop":
            label = next((stop_idx[k] for k in range(a, b + 1) if k in stop_idx), None) or reason[a] or "일시정지"
            detail = {"출발": "주변 확인 후 천천히 출발", "도착": "목적지 · 안전한 곳에 정차"}.get(
                label, "정차·일시정지 후 안전 확인" if "교차로" in label else "체크포인트 · 전망 정차, 스탬프 자동 적립")
            out.append(Guidance("stop", a * DS, ll[a][0], ll[a][1], f"정지 · {label}", detail, 0))
        prev_kind = z["kind"]
    return out
