"""Magma Performance Data Lab: aggregate driving data into product-development insights.

Two sources:
* ``synthetic_fleet()`` – deterministic SYNTHETIC records across the Genesis line-up so the
  dashboard is meaningful in a demo. Clearly labelled as synthetic everywhere it is shown.
* the user's own reports – used only after explicit opt-in consent.

Only aggregates leave this module. No timestamps, traces or locations are exported.
"""
from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Mapping, Sequence

from .catalog import TAG_LABELS

FLEET_SIZE = 360
MIN_GROUP = 5  # k-anonymity style floor: groups smaller than this are not shown


@dataclass(frozen=True)
class DriveRecord:
    model: str
    powertrain: str          # "EV" | "ICE"
    ps_per_ton: float
    weight_kg: int
    overall: int
    accel: int
    braking: int
    cornering: int
    harsh_accel_per10: float
    harsh_brake_per10: float
    lat_g_p95: float
    preferred_tag: str
    hr_post_event_delta: int | None


def _clamp(x: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, x))


@lru_cache(maxsize=4)
def synthetic_fleet(presets_key: tuple, seed: int = 42) -> tuple[DriveRecord, ...]:
    """presets_key: tuple of (model, power_ps, weight_kg, transmission) tuples (hashable for caching)."""
    rng = random.Random(seed)
    weights = [3 if "Magma" in m else 2 if m.startswith(("G80", "GV80", "GV70")) else 1
               for m, *_ in presets_key]
    tags_ev = ["mountain", "hairpin", "view", "coastal", "gt"]
    tags_ice = ["gt", "coastal", "view", "mountain", "urban"]
    out = []
    for _ in range(FLEET_SIZE):
        model, ps, kg, trans = rng.choices(presets_key, weights=weights)[0]
        pw = ps / (kg / 1000)
        skill = rng.gauss(0, 1)
        acc_rate = max(0.0, 1.2 + 0.010 * (pw - 170) - 0.5 * skill + rng.gauss(0, 0.4))
        brk_rate = max(0.0, 2.6 + 0.0015 * (kg - 1800) - 0.6 * skill + rng.gauss(0, 0.5))
        lat = _clamp(0.29 + 0.045 * skill + 0.0003 * (pw - 170) + rng.gauss(0, 0.035), 0.1, 0.7)
        corner_rate = max(0.0, (lat - 0.26) * 15 + rng.gauss(0, 0.4))
        accel = round(_clamp(100 - 6 * acc_rate - rng.uniform(0, 4)))
        braking = round(_clamp(100 - 8 * brk_rate - rng.uniform(0, 6)))
        cornering = round(_clamp(100 - 12 * corner_rate - 120 * max(0.0, lat - 0.28)))
        tagpool = tags_ev if trans == "EV" else tags_ice
        tag = rng.choices(tagpool, weights=[5, 4, 3, 2, 2] if trans == "EV" else [4, 3, 3, 3, 1])[0]
        hr_delta = round(max(0.0, 2 + 1.5 * brk_rate + rng.gauss(0, 1.5))) if rng.random() < 0.55 else None
        out.append(DriveRecord(model=model, powertrain="EV" if trans == "EV" else "ICE",
                               ps_per_ton=round(pw, 1), weight_kg=kg,
                               overall=round((accel + braking + cornering) / 3), accel=accel,
                               braking=braking, cornering=cornering,
                               harsh_accel_per10=round(acc_rate, 2), harsh_brake_per10=round(brk_rate, 2),
                               lat_g_p95=round(lat, 3), preferred_tag=tag, hr_post_event_delta=hr_delta))
    return tuple(out)


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = _mean(xs), _mean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def aggregate(records: Iterable[DriveRecord]) -> dict:
    recs = list(records)
    if not recs:
        return {"n": 0}
    by_model: dict[str, list[DriveRecord]] = defaultdict(list)
    for r in recs:
        by_model[r.model].append(r)

    models = []
    for model, rs in by_model.items():
        if len(rs) < MIN_GROUP:
            continue
        brake_rate = _mean([r.harsh_brake_per10 for r in rs])
        weight = _mean([r.weight_kg for r in rs])
        models.append({
            "model": model,
            "n": len(rs),
            "powertrain": rs[0].powertrain,
            "overall": round(_mean([r.overall for r in rs])),
            "braking": round(_mean([r.braking for r in rs])),
            "cornering": round(_mean([r.cornering for r in rs])),
            "harsh_accel_per10": round(_mean([r.harsh_accel_per10 for r in rs]), 2),
            "harsh_brake_per10": round(brake_rate, 2),
            "lat_g_p95": round(_mean([r.lat_g_p95 for r in rs]), 2),
            # Relative brake thermal-load proxy: mass x harsh-braking frequency (unitless index)
            "brake_load_index": round(weight / 1000 * brake_rate, 2),
        })
    models.sort(key=lambda m: -m["n"])

    tag_counts = Counter(r.preferred_tag for r in recs)
    total = sum(tag_counts.values())
    tags = [{"tag": t, "label": TAG_LABELS.get(t, t), "share": round(100 * c / total)}
            for t, c in tag_counts.most_common()]

    weakest = Counter(min((("가속 제어", r.accel), ("제동 안정성", r.braking), ("코너링 안정성", r.cornering)),
                          key=lambda kv: kv[1])[0] for r in recs)
    hr = [r.hr_post_event_delta for r in recs if r.hr_post_event_delta is not None]
    scatter = [(r.ps_per_ton, r.harsh_accel_per10) for r in recs]
    corr = _pearson([p for p, _ in scatter], [a for _, a in scatter])
    ev = [r for r in recs if r.powertrain == "EV"]
    ice = [r for r in recs if r.powertrain == "ICE"]

    return {
        "n": len(recs),
        "models": models,
        "tags": tags,
        "weakest": [{"label": k, "share": round(100 * v / len(recs))} for k, v in weakest.most_common()],
        "corr_pw_accel": round(corr, 2) if corr is not None else None,
        "scatter": scatter,
        "hr_n": len(hr),
        "hr_delta_avg": round(_mean(hr), 1) if hr else None,
        "ev_mountain_share": _share(ev, ("mountain", "hairpin")),
        "ice_mountain_share": _share(ice, ("mountain", "hairpin")),
    }


def _share(recs: Sequence[DriveRecord], tags: tuple[str, ...]) -> int | None:
    if len(recs) < MIN_GROUP:
        return None
    return round(100 * sum(1 for r in recs if r.preferred_tag in tags) / len(recs))


def insights(agg: Mapping) -> list[dict]:
    """Rule-based translation of aggregates into development questions for a performance line."""
    if not agg.get("n"):
        return []
    out: list[dict] = []
    models = agg["models"]
    if models:
        top = max(models, key=lambda m: m["brake_load_index"])
        out.append({"area": "브레이크·열관리",
                    "finding": f"{top['model']}의 제동 열부하 지수가 가장 높음 ({top['brake_load_index']})",
                    "action": "중량 대비 반복 제동 구간에서 브레이크 냉각·배터리 열관리 검증을 우선 진행"})
    if agg.get("ev_mountain_share") is not None and agg.get("ice_mountain_share") is not None:
        out.append({"area": "섀시·조향",
                    "finding": f"전동화 고성능 오너의 산악·헤어핀 코스 선호 {agg['ev_mountain_share']}% "
                               f"(내연기관 {agg['ice_mountain_share']}%)",
                    "action": "저·중속 코너 응답성과 조향 감각 튜닝, 중량 이동 제어를 개발 우선순위로 검토"})
    if agg.get("corr_pw_accel") is not None:
        out.append({"area": "파워트레인 UX",
                    "finding": f"출력 대비 중량과 급가속 빈도의 상관계수 {agg['corr_pw_accel']}",
                    "action": "부스트·런치 기능은 서킷 모드로 한정하고, 공도에서는 부드러운 토크 전달 맵을 기본값으로 설계"})
    if agg.get("weakest"):
        w = agg["weakest"][0]
        out.append({"area": "아카데미·고객 경험",
                    "finding": f"가장 많이 나타난 약점: {w['label']} ({w['share']}%)",
                    "action": "출고 시 맞춤 교육 바우처와 연계해 고성능 모델 인도 경험을 설계"})
    if agg.get("hr_delta_avg") is not None:
        out.append({"area": "운전자 부하 (웨어러블)",
                    "finding": f"급조작 직후 심박 상승 평균 +{agg['hr_delta_avg']} bpm (웨어러블 연동 {agg['hr_n']}명)",
                    "action": "운전자 피로·긴장도를 고려한 주행 모드·실내 음향(ASD) 튜닝 검증 지표로 활용"})
    return out


def record_from_report(result: Mapping, model: str, transmission: str | None,
                       power_ps: int | None, weight_kg: int | None, preferred_tag: str) -> DriveRecord | None:
    """Convert one stored report into an anonymised DriveRecord (no time/location fields)."""
    m = result.get("metrics", {})
    if not power_ps or not weight_kg:
        return None
    dist = max(result.get("distance_km") or 0.0, 0.1)
    ev = result.get("events", {})
    heart = result.get("heart") or {}
    return DriveRecord(
        model=model, powertrain="EV" if transmission == "EV" else "ICE",
        ps_per_ton=round(power_ps / (weight_kg / 1000), 1), weight_kg=weight_kg,
        overall=int(result.get("overall", 0)),
        accel=int(m.get("accel", {}).get("score") or 0),
        braking=int(m.get("braking", {}).get("score") or 0),
        cornering=int(m.get("cornering", {}).get("score") or 0),
        harsh_accel_per10=round(10 * ev.get("harsh_accel", 0) / dist, 2),
        harsh_brake_per10=round(10 * ev.get("harsh_brake", 0) / dist, 2),
        lat_g_p95=0.0, preferred_tag=preferred_tag,
        hr_post_event_delta=heart.get("post_event_delta"),
    )


def scatter_svg_points(scatter: Sequence[tuple[float, float]], width: int = 600, height: int = 260,
                       pad: int = 30) -> dict:
    if not scatter:
        return {"points": [], "xmax": 0, "ymax": 0}
    xs = [p for p, _ in scatter]
    ys = [a for _, a in scatter]
    x0, x1 = min(xs), max(xs)
    y1 = max(max(ys), 0.1)
    xspan = (x1 - x0) or 1.0
    pts = [(round(pad + (x - x0) / xspan * (width - 2 * pad), 1),
            round(height - pad - y / y1 * (height - 2 * pad), 1)) for x, y in scatter]
    return {"points": pts, "xmin": round(x0), "xmax": round(x1), "ymax": round(y1, 1),
            "width": width, "height": height, "pad": pad}
