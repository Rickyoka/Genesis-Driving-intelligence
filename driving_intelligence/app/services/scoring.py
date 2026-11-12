"""Rule-based Fun Driving Score.

Design principles (from the business plan):
* Speed or exceeding limits is NEVER a scoring input.
* Public-road guidance is limited to safe operation (smooth inputs, engine braking).
* Every adjustment is explained so the result is auditable, not a black box.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterable

from .catalog import Course
from .learning import Personalization, apply_shift

MODES = MappingProxyType({
    "sport": {
        "label": "Sport",
        "desc": "곡선·고저차 등 운전 재미를 중시하되, 제한속도·시야·노면 안전을 함께 평가합니다.",
        "weights": {"fun": 0.40, "comfort": 0.15, "scenery": 0.10, "amenity": 0.05, "safety": 0.20, "distance": 0.10},
        "range_km": (5, 30, 60),
    },
    "touring": {
        "label": "Touring",
        "desc": "경관과 휴게 시설, 여유로운 리듬을 중시하는 주말 드라이브 모드입니다.",
        "weights": {"fun": 0.15, "comfort": 0.20, "scenery": 0.30, "amenity": 0.15, "safety": 0.15, "distance": 0.05},
        "range_km": (10, 60, 100),
    },
    "gt": {
        "label": "GT",
        "desc": "노면 품질·도로 폭·장거리 크루징의 편안함을 중시합니다.",
        "weights": {"fun": 0.15, "comfort": 0.35, "scenery": 0.15, "amenity": 0.10, "safety": 0.15, "distance": 0.10},
        "range_km": (30, 200, 200),
    },
})

WEATHERS = MappingProxyType({"clear": "맑음", "rain": "비 (젖은 노면)", "snow": "눈·결빙"})

COMPONENT_LABELS = MappingProxyType({
    "fun": "운전 재미 (곡률·헤어핀·고저차)",
    "comfort": "주행 편안함 (노면·도로 폭·혼잡도)",
    "scenery": "경관",
    "amenity": "휴게·편의 시설",
    "safety": "안전 여유 (도로 위험도 역산)",
    "distance": "모드 적합 거리",
})

PUBLIC_ROAD_GUIDE = (
    "공도에서는 제한속도와 교통 법규가 항상 우선입니다. 이 앱은 빠른 주행을 점수로 평가하지 않습니다.",
    "코너는 진입 전 직선에서 충분히 감속하고, 코너 안에서는 일정한 속도로 통과하세요.",
    "블라인드 코너에서는 차로 중앙을 지키고 중앙선을 넘지 마세요.",
    "긴 내리막은 저단 기어로 엔진 브레이크를 활용해 브레이크 과열(페이드)을 예방하세요.",
    "제동 지점·코너 진입 속도 같은 성능 코칭은 폐쇄 서킷 전용 모드에서만 제공합니다.",
)

WIDTH_SCORE = MappingProxyType({"narrow": 0.0, "medium": 0.6, "wide": 1.0})
RWD_LAYOUTS = frozenset({"FR", "MR", "RR"})


@dataclass(frozen=True)
class VehicleProfile:
    power_ps: int
    torque_nm: int
    weight_kg: int
    drivetrain: str
    transmission: str
    size_class: str

    @property
    def ps_per_ton(self) -> float:
        return self.power_ps / (self.weight_kg / 1000.0) if self.weight_kg > 0 else 0.0

    @property
    def is_heavy(self) -> bool:
        return self.weight_kg >= 1800 or self.size_class == "large"


@dataclass
class CourseScore:
    course: Course
    score: int
    base_score: int
    components: dict[str, float]
    weights: dict[str, float] = field(default_factory=dict)
    adjustments: list[tuple[str, int]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    excluded_reason: str | None = None

    @property
    def recommended(self) -> bool:
        return self.excluded_reason is None and self.score >= 60

    @property
    def grade(self) -> str:
        if self.excluded_reason:
            return "추천 제외"
        if self.score >= 75:
            return "강력 추천"
        if self.score >= 60:
            return "추천"
        if self.score >= 45:
            return "보통"
        return "비추천"


def _n5(x: int) -> float:
    """Normalise a 1..5 rating to 0..1."""
    return (x - 1) / 4.0


def _range_fit(length: float, lo: float, hi: float, tail: float) -> float:
    """1.0 inside [lo, hi]; linear fall-off outside."""
    if length < lo:
        return max(0.0, length / lo)
    if length > hi:
        span = max(tail, 1.0)
        return max(0.0, 1.0 - (length - hi) / span)
    return 1.0


def _components(course: Course, mode: str) -> dict[str, float]:
    lo, hi, tail = MODES[mode]["range_km"]
    return {
        "fun": 0.45 * _n5(course.curve_level) + 0.25 * (course.hairpin / 3.0)
               + 0.30 * min(course.elevation_m / 1000.0, 1.0),
        "comfort": 0.5 * _n5(course.surface) + 0.3 * WIDTH_SCORE[course.width]
                   + 0.2 * (1.0 - _n5(course.congestion)),
        "scenery": _n5(course.scenery),
        "amenity": _n5(course.facilities),
        "safety": 1.0 - _n5(course.risk),
        "distance": _range_fit(course.length_km, lo, hi, tail),
    }


def score_course(course: Course, mode: str = "sport", vehicle: VehicleProfile | None = None,
                 weather: str = "clear", night: bool = False,
                 personal: Personalization | None = None) -> CourseScore:
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    if weather not in WEATHERS:
        raise ValueError(f"unknown weather: {weather}")

    comps = _components(course, mode)
    weights = MODES[mode]["weights"]
    if personal is not None and personal.weight_shift:
        weights = apply_shift(weights, personal.weight_shift)
    base = sum(weights[k] * comps[k] for k in weights) * 100.0
    adjustments: list[tuple[str, int]] = []
    notes: list[str] = []
    excluded: str | None = None

    # ---- vehicle fit -------------------------------------------------------
    if vehicle is not None:
        if vehicle.is_heavy and course.width == "narrow":
            adjustments.append(("대형·중량 차량과 좁은 노폭", -10))
            notes.append("차체가 크고 무거워 좁은 구간 교행이 부담됩니다. 마주 오는 차량이 보이면 미리 정차 공간을 확보하세요.")
        elif vehicle.is_heavy and course.hairpin >= 3:
            adjustments.append(("중량 차량과 연속 헤어핀", -6))
        if mode == "gt" and vehicle.is_heavy and course.width != "narrow":
            adjustments.append(("GT 성향 차량과 여유 있는 도로 폭", +4))
        if mode == "sport" and vehicle.weight_kg <= 1500 and course.curve_level >= 4 \
                and course.width != "narrow":
            adjustments.append(("경량 차량의 민첩한 차량 특성", +3))
        if vehicle.drivetrain in RWD_LAYOUTS and course.surface <= 2:
            adjustments.append(("후륜구동과 노면 불량", -5))
            notes.append("후륜구동 차량은 노면이 고르지 않을 때 가속 중 트랙션을 잃기 쉽습니다. 코너 탈출 시 가속 페달을 천천히 여세요.")
        if vehicle.drivetrain == "FF" and course.hairpin >= 2:
            notes.append("전륜구동 차량은 헤어핀에서 언더스티어가 생기기 쉽습니다. 진입 전 감속을 마치세요.")
        if vehicle.transmission == "MT" and course.hairpin >= 2 and course.elevation_m >= 400:
            notes.append("수동변속: 급경사 정차 후 출발(언덕 출발)에 대비하고, 내리막은 2~3단으로 엔진 브레이크를 사용하세요.")
        if vehicle.transmission in ("AT", "DCT") and course.elevation_m >= 500:
            notes.append("자동·DCT: 긴 내리막에서는 수동 모드로 저단을 고정해 엔진 브레이크를 활용하세요.")
        if vehicle.transmission == "EV":
            if course.elevation_m >= 400:
                notes.append("전기차: 긴 내리막에서는 회생제동 강도를 높여 속도를 관리하면 마찰 브레이크 부담을 줄일 수 있습니다.")
            if vehicle.power_ps >= 500:
                notes.append("부스트·드리프트·런치 컨트롤 같은 고성능 기능은 공도가 아닌 폐쇄 서킷에서만 사용하세요.")
        if mode == "sport" and vehicle.ps_per_ton >= 250:
            notes.append(f"출력 대비 중량이 가벼운 차량입니다(약 {vehicle.ps_per_ton:.0f} PS/t). "
                         "공도에서는 가속 성능을 쓰지 말고 제한속도 안에서 리듬감 있게 주행하세요.")

    # ---- weather -------------------------------------------------------------
    if weather == "rain":
        penalty = 2 * course.curve_level + 2 * course.hairpin
        adjustments.append(("젖은 노면", -penalty))
        notes.append("젖은 노면: 제동거리가 늘어납니다. 평소보다 차간거리를 두 배 이상 확보하세요.")
        if vehicle is not None and vehicle.drivetrain in RWD_LAYOUTS:
            adjustments.append(("젖은 노면과 후륜구동", -3))
    elif weather == "snow":
        if course.winter_caution:
            excluded = "동절기 결빙·통제 위험이 큰 구간이라 눈·결빙 조건에서는 추천하지 않습니다."
        else:
            penalty = 15
            if vehicle is not None and vehicle.drivetrain == "AWD":
                penalty = 8
            adjustments.append(("눈·결빙 노면", -penalty))
            notes.append("결빙 노면: 겨울용 타이어가 필수입니다. 급조작을 피하세요.")

    # ---- time of day ---------------------------------------------------------
    if night:
        if course.night_ok:
            adjustments.append(("야경 코스", +3))
        else:
            adjustments.append(("야간 비추천 (조명 부족·블라인드 코너)", -10))

    if personal is not None:
        bonus = personal.course_bonus(course)
        if bonus is not None:
            adjustments.append(bonus)

    if course.risk >= 4:
        notes.append("도로 위험도가 높은 코스입니다. 블라인드 코너가 많아 차로 중앙 유지와 감속이 필수입니다.")

    final = int(round(max(0.0, min(100.0, base + sum(d for _, d in adjustments)))))
    return CourseScore(
        course=course,
        score=final,
        base_score=int(round(base)),
        weights=weights,
        components={k: round(v * 100) for k, v in comps.items()},
        adjustments=adjustments,
        notes=notes,
        excluded_reason=excluded,
    )


def rank_courses(courses: Iterable[Course], mode: str, vehicle: VehicleProfile | None,
                 weather: str, night: bool, personal: Personalization | None = None) -> list[CourseScore]:
    scored = [score_course(c, mode, vehicle, weather, night, personal) for c in courses]
    scored.sort(key=lambda s: (s.excluded_reason is not None, -s.score, s.course.name))
    return scored
