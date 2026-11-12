"""Static catalog loading (courses, vehicle presets, education programs).

Data files are loaded once at startup, validated, and exposed as immutable
objects so request handlers never re-read or mutate them.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

DRIVETRAINS = ("FF", "FR", "MR", "RR", "AWD")
TRANSMISSIONS = ("MT", "AT", "DCT", "EV")
SIZE_CLASSES = ("compact", "mid", "large")
WIDTHS = ("narrow", "medium", "wide")

DRIVETRAIN_LABELS = MappingProxyType({
    "FF": "전륜구동 (FF)", "FR": "후륜구동 (FR)", "MR": "미드십 후륜 (MR)",
    "RR": "리어엔진 후륜 (RR)", "AWD": "사륜구동 (AWD)",
})
TRANSMISSION_LABELS = MappingProxyType({"MT": "수동 (MT)", "AT": "자동 (AT)", "DCT": "듀얼클러치 (DCT)",
                                         "EV": "전기 (감속기·VGS)"})
SIZE_LABELS = MappingProxyType({"compact": "소형·쿠페", "mid": "중형", "large": "대형"})
WIDTH_LABELS = MappingProxyType({"narrow": "좁음", "medium": "보통", "wide": "넓음"})
TAG_LABELS = MappingProxyType({
    "urban": "도심 근교", "view": "전망", "night": "야간", "mountain": "산악",
    "hairpin": "헤어핀", "coastal": "해안", "gt": "장거리",
})


class CatalogError(RuntimeError):
    """Raised when a bundled data file is missing or malformed."""


@dataclass(frozen=True)
class Checkpoint:
    label: str
    lat: float
    lng: float
    radius_m: int


@dataclass(frozen=True)
class Waypoint:
    label: str
    lat: float
    lng: float


@dataclass(frozen=True)
class Course:
    id: str
    name: str
    region: str
    tags: tuple[str, ...]
    length_km: float
    curve_level: int
    hairpin: int
    elevation_m: int
    surface: int
    scenery: int
    facilities: int
    congestion: int
    risk: int
    width: str
    night_ok: bool
    winter_caution: bool
    highlights: str
    caution: str
    checkpoint: Checkpoint | None = None
    route_start: Waypoint | None = None
    route_end: Waypoint | None = None

    def waypoints(self) -> list[tuple[str, float, float]]:
        """start -> checkpoint (via) -> end, skipping anything missing."""
        out = []
        if self.route_start:
            out.append((self.route_start.label, self.route_start.lat, self.route_start.lng))
        if self.checkpoint:
            out.append((self.checkpoint.label, self.checkpoint.lat, self.checkpoint.lng))
        if self.route_end:
            out.append((self.route_end.label, self.route_end.lat, self.route_end.lng))
        return out


@dataclass(frozen=True)
class Program:
    id: str
    category: str
    title: str
    level: str
    duration: str
    venue: str
    school: str
    summary: str
    targets: tuple[str, ...] = ()
    drivetrains: tuple[str, ...] = ()
    transmissions: tuple[str, ...] = ()
    min_overall: int | None = None


@dataclass(frozen=True)
class Catalog:
    courses: tuple[Course, ...]
    courses_by_id: Mapping[str, Course]
    presets: tuple[Mapping[str, object], ...]
    programs: tuple[Program, ...]
    programs_by_id: Mapping[str, Program]
    course_note: str = ""
    preset_note: str = ""
    program_note: str = ""
    checkpoint_note: str = ""
    route_note: str = ""
    extra: Mapping[str, object] = field(default_factory=dict)


def _read_json(name: str) -> dict:
    path = DATA_DIR / name
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise CatalogError(f"데이터 파일이 없습니다: {name}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"데이터 파일 형식 오류 ({name}): {exc}") from exc
    if not isinstance(data, dict):
        raise CatalogError(f"데이터 파일 최상위는 객체여야 합니다: {name}")
    return data


def _int_in(value: object, lo: int, hi: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise CatalogError(f"{label} 값이 범위({lo}~{hi})를 벗어났습니다: {value!r}")
    return value


def _parse_checkpoint(cid: str, raw: dict | None) -> Checkpoint | None:
    if raw is None:
        return None
    try:
        lat, lng = float(raw["lat"]), float(raw["lng"])
        radius = int(raw["radius_m"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CatalogError(f"{cid}: checkpoint 형식 오류") from exc
    # Bounding box of the Korean peninsula + Jeju; catches swapped lat/lng typos.
    if not in_korea(lat, lng):
        raise CatalogError(f"{cid}: checkpoint 좌표가 국내 범위를 벗어났습니다 ({lat}, {lng})")
    if not 100 <= radius <= 5000:
        raise CatalogError(f"{cid}: checkpoint 반경은 100~5000 m 범위여야 합니다")
    return Checkpoint(label=str(raw.get("label", "")), lat=lat, lng=lng, radius_m=radius)


def in_korea(lat: float, lng: float) -> bool:
    """Bounding box of the Korean peninsula + Jeju (catches swapped lat/lng typos)."""
    return 33.0 <= lat <= 38.7 and 124.5 <= lng <= 131.0


def _parse_waypoint(cid: str, raw: dict | None) -> Waypoint | None:
    if raw is None:
        return None
    try:
        wp = Waypoint(label=str(raw.get("label", "")), lat=float(raw["lat"]), lng=float(raw["lng"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise CatalogError(f"{cid}: route 형식 오류") from exc
    if not in_korea(wp.lat, wp.lng):
        raise CatalogError(f"{cid}: route 좌표가 국내 범위를 벗어났습니다")
    return wp


def _parse_course(raw: dict) -> Course:
    try:
        cid = str(raw["id"])
        width = raw["width"]
        if width not in WIDTHS:
            raise CatalogError(f"{cid}: width 값 오류 {width!r}")
        length = float(raw["length_km"])
        if not 0 < length <= 1000:
            raise CatalogError(f"{cid}: length_km 범위 오류")
        tags = tuple(str(t) for t in raw.get("tags", ()))
        return Course(
            id=cid,
            name=str(raw["name"]),
            region=str(raw["region"]),
            tags=tags,
            length_km=length,
            curve_level=_int_in(raw["curve_level"], 1, 5, f"{cid}.curve_level"),
            hairpin=_int_in(raw["hairpin"], 0, 3, f"{cid}.hairpin"),
            elevation_m=_int_in(raw["elevation_m"], 0, 2000, f"{cid}.elevation_m"),
            surface=_int_in(raw["surface"], 1, 5, f"{cid}.surface"),
            scenery=_int_in(raw["scenery"], 1, 5, f"{cid}.scenery"),
            facilities=_int_in(raw["facilities"], 1, 5, f"{cid}.facilities"),
            congestion=_int_in(raw["congestion"], 1, 5, f"{cid}.congestion"),
            risk=_int_in(raw["risk"], 1, 5, f"{cid}.risk"),
            width=width,
            night_ok=bool(raw["night_ok"]),
            winter_caution=bool(raw["winter_caution"]),
            highlights=str(raw.get("highlights", "")),
            caution=str(raw.get("caution", "")),
            checkpoint=_parse_checkpoint(cid, raw.get("checkpoint")),
            route_start=_parse_waypoint(cid, (raw.get("route") or {}).get("start")),
            route_end=_parse_waypoint(cid, (raw.get("route") or {}).get("end")),
        )
    except KeyError as exc:
        raise CatalogError(f"코스 데이터 필드 누락: {exc}") from exc


def _parse_program(raw: dict) -> Program:
    try:
        min_overall = raw.get("min_overall")
        return Program(
            id=str(raw["id"]),
            category=str(raw["category"]),
            title=str(raw["title"]),
            level=str(raw["level"]),
            duration=str(raw["duration"]),
            venue=str(raw["venue"]),
            school=str(raw["school"]),
            summary=str(raw["summary"]),
            targets=tuple(raw.get("targets", ())),
            drivetrains=tuple(raw.get("drivetrains", ())),
            transmissions=tuple(raw.get("transmissions", ())),
            min_overall=int(min_overall) if min_overall is not None else None,
        )
    except KeyError as exc:
        raise CatalogError(f"교육 프로그램 필드 누락: {exc}") from exc


def _validate_preset(raw: dict) -> Mapping[str, object]:
    required = ("key", "maker", "model", "engine", "power_ps", "torque_nm",
                "drivetrain", "transmission", "weight_kg", "size_class")
    missing = [k for k in required if k not in raw]
    if missing:
        raise CatalogError(f"차량 프리셋 필드 누락: {missing}")
    if raw["drivetrain"] not in DRIVETRAINS or raw["transmission"] not in TRANSMISSIONS \
            or raw["size_class"] not in SIZE_CLASSES:
        raise CatalogError(f"차량 프리셋 코드 값 오류: {raw['key']}")
    return MappingProxyType({k: raw[k] for k in required})


def load_catalog() -> Catalog:
    """Load and validate every bundled data file. Raises CatalogError on bad data."""
    courses_doc = _read_json("courses.json")
    presets_doc = _read_json("vehicle_presets.json")
    programs_doc = _read_json("programs.json")

    courses = tuple(_parse_course(c) for c in courses_doc.get("courses", []))
    ids = [c.id for c in courses]
    if len(ids) != len(set(ids)):
        raise CatalogError("코스 id가 중복되었습니다.")
    programs = tuple(_parse_program(p) for p in programs_doc.get("programs", []))
    presets = tuple(_validate_preset(p) for p in presets_doc.get("presets", []))

    logger.info("Catalog loaded: %d courses, %d presets, %d programs",
                len(courses), len(presets), len(programs))
    return Catalog(
        courses=courses,
        courses_by_id=MappingProxyType({c.id: c for c in courses}),
        presets=presets,
        programs=programs,
        programs_by_id=MappingProxyType({p.id: p for p in programs}),
        course_note=str(courses_doc.get("_meta", {}).get("note", "")),
        preset_note=str(presets_doc.get("_meta", {}).get("note", "")),
        program_note=str(programs_doc.get("_meta", {}).get("note", "")),
        checkpoint_note=str(courses_doc.get("_meta", {}).get("checkpoint_note", "")),
        route_note=str(courses_doc.get("_meta", {}).get("route_note", "")),
    )
