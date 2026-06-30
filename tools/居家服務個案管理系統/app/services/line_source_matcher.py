from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.case import Case, CaseStatus
from app.models.line_source_link import LineSourceCategory, LineSourceKind, LineSourceLink
from app.models.service_schedule import ServiceSchedule
from app.models.user import User, UserRole


@dataclass
class LineSourceCandidate:
    case: Case
    reason: str
    score: int


def source_kind_from_line_type(source_type: str | None) -> LineSourceKind:
    if source_type == "group":
        return LineSourceKind.group
    if source_type == "room":
        return LineSourceKind.room
    return LineSourceKind.user


def infer_category(display_name: str | None, kind: LineSourceKind) -> LineSourceCategory:
    name = (display_name or "").strip()
    if name.startswith("@") or name.startswith("＠"):
        return LineSourceCategory.case_family
    if name.startswith("!") or name.startswith("！"):
        return LineSourceCategory.caregiver
    if name.startswith("*") or name.startswith("＊"):
        return LineSourceCategory.organization
    if kind == LineSourceKind.user:
        return LineSourceCategory.case_family
    return LineSourceCategory.unknown


def normalize_line_name(value: str | None) -> str:
    text = (value or "").strip()
    if text[:1] in {"@", "＠", "!", "！", "*", "＊"}:
        text = text[1:]
    for char in "/｜|-_()（）[]【】、,，.。:：":
        text = text.replace(char, " ")
    return " ".join(text.split())


def _contains_name(haystack: str, needle: str | None) -> bool:
    needle = (needle or "").strip()
    return bool(needle and needle in haystack)


def candidate_cases_for_source(db: Session, display_name: str | None, category: LineSourceCategory) -> list[LineSourceCandidate]:
    normalized = normalize_line_name(display_name)
    candidates: dict[str, LineSourceCandidate] = {}
    if not normalized:
        return []

    for case in db.query(Case).filter(Case.status != CaseStatus.closed).all():
        score = 0
        reasons = []
        if _contains_name(normalized, case.name):
            score += 100
            reasons.append(f"符合個案姓名：{case.name}")
        if category == LineSourceCategory.caregiver:
            caregiver_schedules = [
                schedule for schedule in case.service_schedules
                if schedule.caregiver and _contains_name(normalized, schedule.caregiver.display_name)
            ]
            if caregiver_schedules:
                score += 60
                names = sorted({schedule.caregiver.display_name for schedule in caregiver_schedules})
                reasons.append(f"符合服務居服員：{'、'.join(names)}")
        if score:
            candidates[case.id] = LineSourceCandidate(case=case, reason="；".join(reasons), score=score)

    return sorted(candidates.values(), key=lambda item: item.score, reverse=True)


def candidate_caregivers_for_source(db: Session, display_name: str | None) -> list[User]:
    normalized = normalize_line_name(display_name)
    if not normalized:
        return []
    return [
        user for user in db.query(User).filter(User.role == UserRole.caregiver, User.is_active.is_(True)).all()
        if _contains_name(normalized, user.display_name)
    ]


def apply_auto_source_match(db: Session, source: LineSourceLink) -> bool:
    """Auto-fill a LINE source only when the match is unique and explicit."""
    if source.case_id or source.caregiver_user_id:
        return False

    raw_name = (source.display_name or "").strip()
    normalized = normalize_line_name(source.display_name)
    if not normalized:
        return False

    has_explicit_marker = raw_name[:1] in {"@", "＠", "!", "！", "*", "＊"}
    if source.source_kind != LineSourceKind.user and not has_explicit_marker:
        return False

    if raw_name[:1] in {"*", "＊"}:
        source.category = LineSourceCategory.organization
        source.relation_label = source.relation_label or "A單位"
        return True

    caregivers = candidate_caregivers_for_source(db, source.display_name)
    case_candidates = [
        candidate
        for candidate in candidate_cases_for_source(db, source.display_name, LineSourceCategory.case_family)
        if candidate.score >= 100
    ]

    if raw_name[:1] in {"@", "＠"}:
        caregivers = []
    elif raw_name[:1] in {"!", "！"}:
        case_candidates = []

    if len(caregivers) == 1 and not case_candidates:
        source.category = LineSourceCategory.caregiver
        source.caregiver_user_id = caregivers[0].id
        source.relation_label = source.relation_label or "居服員"
        return True

    if len(case_candidates) == 1 and not caregivers:
        source.category = LineSourceCategory.case_family
        source.case_id = case_candidates[0].case.id
        source.relation_label = source.relation_label or "個案／家屬"
        return True

    return False


def get_or_create_line_source(
    db: Session,
    source_kind: LineSourceKind,
    source_id: str,
    display_name: str | None = None,
) -> LineSourceLink:
    source = db.query(LineSourceLink).filter(LineSourceLink.source_id == source_id).first()
    if source:
        if display_name and not source.display_name:
            source.display_name = display_name
            source.category = infer_category(display_name, source_kind)
            apply_auto_source_match(db, source)
        return source
    source = LineSourceLink(
        source_kind=source_kind,
        source_id=source_id,
        display_name=display_name,
        category=infer_category(display_name, source_kind),
    )
    db.add(source)
    db.flush()
    apply_auto_source_match(db, source)
    return source
