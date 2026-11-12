"""Learned navigation (prototype): personalise course scoring from the driver's own history.

Rule-based and fully explainable, as the business plan recommends for the first
phase. Inputs are only the driver's recent analysis metrics and saved courses;
every effect is surfaced as a labelled score adjustment.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterable, Mapping

from .catalog import TAG_LABELS, Course

RECENT_REPORTS = 5
MAX_TAG_BONUS = 4


@dataclass(frozen=True)
class Personalization:
    weight_shift: Mapping[str, float] = field(default_factory=dict)
    tag_bonus: Mapping[str, int] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    reports_used: int = 0
    favorites_used: int = 0

    @property
    def active(self) -> bool:
        return bool(self.weight_shift or self.tag_bonus)

    def course_bonus(self, course: Course) -> tuple[str, int] | None:
        best = max(((self.tag_bonus.get(t, 0), t) for t in course.tags), default=(0, ""))
        if best[0] <= 0:
            return None
        return f"학습: 선호 유형 '{TAG_LABELS.get(best[1], best[1])}'", best[0]


EMPTY = Personalization()


def _avg(values: list[int]) -> float | None:
    return sum(values) / len(values) if values else None


def build(recent_metrics: Iterable[Mapping[str, dict]], favorites: Iterable[Course]) -> Personalization:
    """recent_metrics: newest-first list of report['metrics'] dicts."""
    metrics_list = list(recent_metrics)[:RECENT_REPORTS]
    favs = list(favorites)
    shift: dict[str, float] = {}
    reasons: list[str] = []

    def collect(key: str) -> float | None:
        return _avg([m[key]["score"] for m in metrics_list
                     if key in m and m[key].get("score") is not None])

    braking, cornering = collect("braking"), collect("cornering")
    overalls = [round(sum(v["score"] for v in m.values() if v.get("score") is not None)
                      / max(1, sum(1 for v in m.values() if v.get("score") is not None)))
                for m in metrics_list]
    overall = _avg(overalls)

    weak_control = [name for name, val in (("제동 안정성", braking), ("코너링 안정성", cornering))
                    if val is not None and val < 70]
    if weak_control:
        shift["fun"] = shift.get("fun", 0.0) - 0.10
        shift["safety"] = shift.get("safety", 0.0) + 0.10
        reasons.append(f"최근 {', '.join(weak_control)} 지표가 70점 미만 → 위험도 낮은 코스를 우선 추천")
    elif overall is not None and overall >= 80 and len(metrics_list) >= 2:
        shift["safety"] = shift.get("safety", 0.0) - 0.05
        shift["fun"] = shift.get("fun", 0.0) + 0.05
        reasons.append("안정성 지표가 꾸준히 80점 이상 → 와인딩 비중을 소폭 확대")

    counts = Counter(t for c in favs for t in c.tags)
    bonus: dict[str, int] = {}
    for tag, n in counts.most_common(2):
        if n >= 1:
            bonus[tag] = min(MAX_TAG_BONUS, 2 * n)
    if bonus:
        labels = ", ".join(TAG_LABELS.get(t, t) for t in bonus)
        reasons.append(f"저장한 코스 {len(favs)}곳의 공통 유형({labels})을 선호로 학습")

    return Personalization(weight_shift=MappingProxyType(shift), tag_bonus=MappingProxyType(bonus),
                           reasons=tuple(reasons), reports_used=len(metrics_list),
                           favorites_used=len(favs))


def apply_shift(weights: Mapping[str, float], shift: Mapping[str, float]) -> dict[str, float]:
    """Move weight between components; never negative, always sums to the original total."""
    out = dict(weights)
    total = sum(out.values())
    for k, d in shift.items():
        out[k] = max(0.0, out.get(k, 0.0) + d)
    s = sum(out.values())
    return {k: v * total / s for k, v in out.items()} if s > 0 else dict(weights)
