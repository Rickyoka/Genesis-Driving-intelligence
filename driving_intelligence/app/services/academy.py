"""Education recommendation and Driving Passport badges.

Recommendations are learning suggestions, not a certification of skill or safety.
App badges are explicitly separate from official course certificates.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .analysis import METRIC_LABELS
from .catalog import Program

IMPROVE_BELOW = 70
MAX_RECOMMENDATIONS = 4
RWD_LAYOUTS = frozenset({"FR", "MR", "RR"})


@dataclass(frozen=True)
class Recommendation:
    program: Program
    reason: str


def recommend(programs: Sequence[Program], metrics: dict, overall: int,
              drivetrain: str | None = None, transmission: str | None = None) -> list[Recommendation]:
    recs: list[Recommendation] = []
    seen: set[str] = set()

    def add(p: Program, reason: str) -> None:
        if p.id not in seen and len(recs) < MAX_RECOMMENDATIONS:
            seen.add(p.id)
            recs.append(Recommendation(p, reason))

    weak = sorted(((k, m["score"]) for k, m in metrics.items()
                   if m.get("score") is not None and m["score"] < IMPROVE_BELOW),
                  key=lambda kv: kv[1])
    for key, score in weak:
        for p in programs:
            if key in p.targets and (not p.drivetrains or drivetrain in p.drivetrains):
                add(p, f"{METRIC_LABELS[key]} {score}점: 개선 권장")

    if drivetrain in RWD_LAYOUTS:
        corner = metrics.get("cornering", {}).get("score")
        accel = metrics.get("accel", {}).get("score")
        if (corner is not None and corner < 80) or (accel is not None and accel < 80):
            for p in programs:
                if p.drivetrains and drivetrain in p.drivetrains:
                    add(p, "후륜 계열 차량 등록 + 가속·코너링 지표 80점 미만")

    if transmission == "MT":
        for p in programs:
            if "MT" in p.transmissions:
                add(p, "수동변속 차량 등록: 레브 매칭·언덕 출발 숙련")

    for p in programs:
        if p.min_overall is not None and overall >= p.min_overall:
            add(p, f"종합 안정성 {overall}점: 다음 단계로 서킷 입문 가능")
    return recs


@dataclass(frozen=True)
class Badge:
    key: str
    title: str
    desc: str
    earned: bool


def compute_badges(report_overalls: Iterable[int], braking_scores: Iterable[int | None],
                   education_count: int) -> list[Badge]:
    """report_overalls must be ordered oldest -> newest."""
    overalls = list(report_overalls)
    brakes = [b for b in braking_scores if b is not None]
    improved = len(overalls) >= 2 and overalls[-1] - overalls[0] >= 5
    return [
        Badge("first", "첫 주행 분석", "주행 기록을 처음 분석했습니다.", len(overalls) >= 1),
        Badge("steady", "꾸준한 기록", "주행 분석 3회 이상.", len(overalls) >= 3),
        Badge("smooth-brake", "부드러운 제동", "제동 안정성 80점 이상 달성.", any(b >= 80 for b in brakes)),
        Badge("growth", "성장 확인", "첫 분석 대비 종합 지표 5점 이상 향상.", improved),
        Badge("learner", "교육 기록 시작", "교육 이수 기록을 1건 이상 등록.", education_count >= 1),
    ]
