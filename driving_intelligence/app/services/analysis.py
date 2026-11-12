"""Rule-based drive analysis from a CSV log (GPS / phone sensor / OBD export).

CSV columns
-----------
required : timestamp_s (seconds, increasing), speed_kmh
optional : accel_long_g (+ = accelerating), accel_lat_g (+/- = left/right),
           heart_rate_bpm (wearable, e.g. Garmin / smartwatch export merged by time)

If accel_long_g is missing it is derived from speed. Without accel_lat_g the
steering and cornering metrics are reported as "데이터 없음" rather than guessed.

All thresholds are prototype values that must be calibrated against real
vehicles and instructor-verified data before production use.
"""
from __future__ import annotations

import csv
import io
import math
from dataclasses import asdict, dataclass

G = 9.80665
MAX_ROWS = 200_000
MIN_ROWS = 30
MIN_DISTANCE_KM = 1.0
MAX_SKIP_RATIO = 0.05
TRACE_POINTS = 300

METRIC_LABELS = {
    "accel": "가속 제어",
    "braking": "제동 안정성",
    "steering": "조향 일관성",
    "following": "차간거리 유지",
    "cornering": "코너링 안정성",
}
METRIC_ORDER = ("accel", "braking", "steering", "following", "cornering")


class AnalysisError(ValueError):
    """User-facing validation error (message is safe to display)."""


@dataclass(frozen=True)
class Thresholds:
    harsh_accel_g: float = 0.30
    harsh_brake_g: float = 0.35
    harsh_corner_g: float = 0.40
    max_gap_s: float = 5.0
    smooth_window_s: float = 0.5


@dataclass
class DriveLog:
    t: list[float]
    speed_kmh: list[float]
    ax: list[float] | None
    ay: list[float] | None
    skipped: int
    hr: list[float | None] | None = None


# --------------------------------------------------------------------------- parsing
def decode_upload(raw: bytes) -> str:
    """Decode uploaded bytes: UTF-8 (with/without BOM), then CP949 for Korean Excel exports."""
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise AnalysisError("파일 인코딩을 읽을 수 없습니다. UTF-8 CSV로 저장해 주세요.")


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        f = float(value)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def parse_csv(text: str) -> DriveLog:
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise AnalysisError("빈 파일입니다.") from None
    cols = {name.strip().lower().lstrip("﻿"): i for i, name in enumerate(header)}
    for req in ("timestamp_s", "speed_kmh"):
        if req not in cols:
            raise AnalysisError(f"필수 열 '{req}'이(가) 없습니다. 샘플 CSV 형식을 참고하세요.")
    i_t, i_v = cols["timestamp_s"], cols["speed_kmh"]
    i_ax, i_ay = cols.get("accel_long_g"), cols.get("accel_lat_g")
    i_hr = cols.get("heart_rate_bpm")
    hr: list[float | None] | None = [] if i_hr is not None else None

    t: list[float] = []
    v: list[float] = []
    ax: list[float] | None = [] if i_ax is not None else None
    ay: list[float] | None = [] if i_ay is not None else None
    total = skipped = 0
    last_t = -math.inf

    for row in reader:
        if not row or all(not c.strip() for c in row):
            continue
        total += 1
        if total > MAX_ROWS:
            raise AnalysisError(f"행 수가 너무 많습니다. 최대 {MAX_ROWS:,}행까지 분석합니다.")

        def cell(idx: int | None) -> str | None:
            return row[idx] if idx is not None and idx < len(row) else None

        tv, sv = _to_float(cell(i_t)), _to_float(cell(i_v))
        axv = _to_float(cell(i_ax)) if ax is not None else 0.0
        ayv = _to_float(cell(i_ay)) if ay is not None else 0.0
        if (tv is None or sv is None or axv is None or ayv is None
                or tv <= last_t or not 0 <= sv <= 400 or abs(axv) > 3 or abs(ayv) > 3):
            skipped += 1
            continue
        last_t = tv
        t.append(tv)
        v.append(sv)
        if ax is not None:
            ax.append(axv)
        if ay is not None:
            ay.append(ayv)
        if hr is not None:
            hv = _to_float(cell(i_hr))  # wearable gaps are common: keep row, drop value
            hr.append(hv if hv is not None and 30 <= hv <= 230 else None)

    if len(t) < MIN_ROWS:
        raise AnalysisError(f"유효한 데이터가 부족합니다 (유효 {len(t)}행, 최소 {MIN_ROWS}행).")
    if total and skipped / total > MAX_SKIP_RATIO:
        raise AnalysisError(f"잘못된 행이 너무 많습니다 ({skipped}/{total}행). 숫자 형식과 시간 순서를 확인하세요.")
    return DriveLog(t=t, speed_kmh=v, ax=ax, ay=ay, skipped=skipped, hr=hr)


# --------------------------------------------------------------------------- math helpers
def _moving_average(values: list[float], window: int) -> list[float]:
    """Centred moving average in O(n) using prefix sums."""
    n = len(values)
    if window <= 1 or n == 0:
        return list(values)
    half = window // 2
    prefix = [0.0] * (n + 1)
    for i, x in enumerate(values):
        prefix[i + 1] = prefix[i] + x
    out = [0.0] * n
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        out[i] = (prefix[hi] - prefix[lo]) / (hi - lo)
    return out


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * pct
    f = math.floor(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def _count_events(signal: list[float], valid: list[bool], threshold: float,
                  t: list[float]) -> list[float]:
    """Count threshold crossings with hysteresis (re-arm below 70 % of threshold).

    threshold > 0 detects signal > threshold; threshold < 0 detects signal < threshold.
    Returns the timestamps where each event started.
    """
    rearm = threshold * 0.7
    in_event = False
    starts: list[float] = []
    for i, x in enumerate(signal):
        if not valid[i]:
            in_event = False
            continue
        over = x > threshold if threshold > 0 else x < threshold
        if not in_event and over:
            in_event = True
            starts.append(t[i])
        elif in_event:
            back = x < rearm if threshold > 0 else x > rearm
            if back:
                in_event = False
    return starts


def _clamp_score(x: float) -> int:
    return int(round(max(0.0, min(100.0, x))))


# --------------------------------------------------------------------------- analysis
def analyze(log: DriveLog, th: Thresholds = Thresholds()) -> dict:
    n = len(log.t)
    t, v_kmh = log.t, log.speed_kmh
    v = [x / 3.6 for x in v_kmh]

    dts = [t[i] - t[i - 1] for i in range(1, n)]
    valid = [False] + [0 < d <= th.max_gap_s for d in dts]  # segment ending at i is usable
    usable_dts = [d for d, ok in zip(dts, valid[1:]) if ok]
    if not usable_dts:
        raise AnalysisError("시간 간격이 너무 깁니다. 샘플링 간격이 5초 이하인 데이터가 필요합니다.")
    median_dt = _percentile(usable_dts, 0.5)
    window = max(1, int(round(th.smooth_window_s / median_dt)))

    distance_m = sum((v[i] + v[i - 1]) / 2 * dts[i - 1] for i in range(1, n) if valid[i])
    duration_s = sum(usable_dts)
    distance_km = distance_m / 1000.0
    if distance_km < MIN_DISTANCE_KM:
        raise AnalysisError(f"주행 거리가 너무 짧습니다 ({distance_km:.2f} km). 최소 {MIN_DISTANCE_KM:.0f} km 이상의 기록이 필요합니다.")

    derived_accel = log.ax is None
    if derived_accel:
        raw_ax = [0.0] + [((v[i] - v[i - 1]) / dts[i - 1] / G) if valid[i] else 0.0 for i in range(1, n)]
    else:
        raw_ax = log.ax  # type: ignore[assignment]
    ax = _moving_average(raw_ax, window)
    ay = _moving_average(log.ay, window) if log.ay is not None else None

    def jerk(series: list[float], mask) -> list[float]:
        out = []
        for i in range(1, n):
            if valid[i] and mask(i):
                out.append(abs(series[i] - series[i - 1]) / dts[i - 1])
        return out

    per10 = 10.0 / distance_km
    accel_events = _count_events(ax, valid, th.harsh_accel_g, t)
    brake_events = _count_events(ax, valid, -th.harsh_brake_g, t)
    accel_jerk = _percentile(jerk(ax, lambda i: ax[i] > 0.05), 0.95)
    brake_jerk = _percentile(jerk(ax, lambda i: ax[i] < -0.05), 0.95)

    metrics: dict[str, dict] = {}
    metrics["accel"] = {
        "score": _clamp_score(100 - 6 * len(accel_events) * per10 - 25 * max(0.0, accel_jerk - 0.6)),
        "detail": f"급가속 {len(accel_events)}회 (10 km당 {len(accel_events) * per10:.1f}회) · 가속 저크 P95 {accel_jerk:.2f} g/s",
    }
    metrics["braking"] = {
        "score": _clamp_score(100 - 8 * len(brake_events) * per10 - 25 * max(0.0, brake_jerk - 0.6)),
        "detail": f"급제동 {len(brake_events)}회 (10 km당 {len(brake_events) * per10:.1f}회) · 제동 저크 P95 {brake_jerk:.2f} g/s",
    }
    corner_events: list[float] = []
    if ay is not None:
        lat_jerk = _percentile(jerk(ay, lambda i: True), 0.95)
        abs_ay = [abs(x) for x, ok in zip(ay, valid) if ok]
        ay_p95 = _percentile(abs_ay, 0.95)
        corner_events = _count_events([abs(x) for x in ay], valid, th.harsh_corner_g, t)
        metrics["steering"] = {
            "score": _clamp_score(100 - 120 * max(0.0, lat_jerk - 0.15)),
            "detail": f"횡방향 저크 P95 {lat_jerk:.2f} g/s (조향 입력의 매끄러움)",
        }
        metrics["cornering"] = {
            "score": _clamp_score(100 - 6 * len(corner_events) * per10 - 60 * max(0.0, ay_p95 - 0.30)),
            "detail": f"급선회 {len(corner_events)}회 · 횡가속도 P95 {ay_p95:.2f} g",
        }
    else:
        for key in ("steering", "cornering"):
            metrics[key] = {"score": None, "detail": "횡가속도(accel_lat_g) 데이터가 없어 평가하지 않았습니다."}
    metrics["following"] = {"score": None, "detail": "전방 차량 거리 데이터(레이더·ADAS)가 필요해 프로토타입에서는 평가하지 않습니다."}

    for key in metrics:
        metrics[key]["label"] = METRIC_LABELS[key]
    ordered = {k: metrics[k] for k in METRIC_ORDER}

    available = [m["score"] for m in ordered.values() if m["score"] is not None]
    overall = int(round(sum(available) / len(available)))
    weakest_key = min((k for k, m in ordered.items() if m["score"] is not None),
                      key=lambda k: ordered[k]["score"])

    return {
        "overall": overall,
        "metrics": ordered,
        "weakest": weakest_key,
        "distance_km": round(distance_km, 2),
        "duration_min": round(duration_s / 60.0, 1),
        "samples": n,
        "sample_rate_hz": round(1.0 / median_dt, 1) if median_dt > 0 else None,
        "skipped_rows": log.skipped,
        "derived_accel": derived_accel,
        "has_lateral": ay is not None,
        "max_speed_kmh": round(max(v_kmh), 1),
        "events": {
            "harsh_accel": len(accel_events),
            "harsh_brake": len(brake_events),
            "harsh_corner": len(corner_events),
        },
        "heart": heart_summary(t, log.hr, brake_events + accel_events + corner_events),
        "trace": build_trace(t, v_kmh, brake_events),
        "thresholds": asdict(th),
    }


def heart_summary(t: list[float], hr: list[float | None] | None, events: list[float],
                  window_s: float = 6.0) -> dict | None:
    """Average / peak heart rate and mean HR in the seconds after harsh events (wearable data)."""
    if hr is None:
        return None
    vals = [h for h in hr if h is not None]
    if len(vals) < 10:
        return None
    avg = sum(vals) / len(vals)
    post: list[float] = []
    if events:
        ev = sorted(events)
        j = 0
        for ti, h in zip(t, hr):
            while j < len(ev) and ev[j] + window_s < ti:
                j += 1
            if h is not None and j < len(ev) and ev[j] <= ti <= ev[j] + window_s:
                post.append(h)
    post_avg = sum(post) / len(post) if post else None
    return {
        "avg": round(avg),
        "max": round(max(vals)),
        "post_event_avg": round(post_avg) if post_avg is not None else None,
        "post_event_delta": round(post_avg - avg) if post_avg is not None else None,
        "coverage_pct": round(100 * len(vals) / len(hr)),
    }


def build_trace(t: list[float], v_kmh: list[float], markers: list[float],
                width: int = 1000, height: int = 220) -> dict:
    """Down-sample a speed trace into SVG polyline coordinates (bucket max keeps peaks)."""
    n = len(t)
    t0, t1 = t[0], t[-1]
    span = (t1 - t0) or 1.0
    vmax = max(max(v_kmh), 1.0)
    buckets = min(TRACE_POINTS, n)
    pts = []
    for b in range(buckets):
        lo = b * n // buckets
        hi = max(lo + 1, (b + 1) * n // buckets)
        idx = max(range(lo, hi), key=lambda i: v_kmh[i])
        x = (t[idx] - t0) / span * width
        y = height - (v_kmh[idx] / vmax) * (height - 10)
        pts.append(f"{x:.1f},{y:.1f}")
    marks = [round((m - t0) / span * width, 1) for m in markers[:100]]
    return {"points": " ".join(pts), "markers": marks, "vmax": round(vmax), "width": width,
            "height": height, "duration_min": round(span / 60.0, 1)}
