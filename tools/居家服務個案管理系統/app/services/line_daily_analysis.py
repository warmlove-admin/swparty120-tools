from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time

from sqlalchemy.orm import Session

from app.models.case import Case
from app.models.line_daily_analysis import LineDailyAnalysis
from app.models.line_message import LineMessage
from app.models.line_source_link import LineSourceCategory, LineSourceLink
from app.models.contact_record import ContactType
from app.models.user import User
from app.services.line_analysis import create_contact_record_from_analysis


IGNORED_LINE_MESSAGE_TYPES = {"sticker"}


@dataclass
class DailyAnalysisResult:
    created: int
    skipped: int
    message_groups: int


def _day_bounds(target_date: date) -> tuple[datetime, datetime]:
    return datetime.combine(target_date, time.min), datetime.combine(target_date, time.max)


def _contact_type_from_source(source: LineSourceLink | None) -> ContactType | None:
    if not source:
        return None
    if source.category == LineSourceCategory.caregiver:
        return ContactType.caregiver
    if source.category == LineSourceCategory.organization:
        return ContactType.organization
    if source.category == LineSourceCategory.case_family:
        relation = source.relation_label or source.display_name or ""
        return ContactType.case_person if "本人" in relation else ContactType.family_contact
    return None


def run_daily_line_analysis(db: Session, target_date: date, actor: User) -> DailyAnalysisResult:
    """將指定日期的 LINE 原始訊息彙整成電訪紀錄。

    這個函式設計給排程與手動按鈕共用；若同一個案/日期/群組已分析過，會略過以避免重複建檔。
    """
    start_at, end_at = _day_bounds(target_date)
    messages = (
        db.query(LineMessage)
        .filter(
            LineMessage.case_id.isnot(None),
            LineMessage.message_type.notin_(IGNORED_LINE_MESSAGE_TYPES),
            LineMessage.event_timestamp >= start_at,
            LineMessage.event_timestamp <= end_at,
        )
        .order_by(LineMessage.event_timestamp.asc(), LineMessage.created_at.asc())
        .all()
    )
    grouped: dict[tuple[str, str | None], list[LineMessage]] = defaultdict(list)
    for message in messages:
        source_identifier = message.line_platform_group_id or message.line_user_id
        grouped[(message.case_id, source_identifier)].append(message)

    created = 0
    skipped = 0
    for (case_id, source_identifier), group_messages in grouped.items():
        existing = db.query(LineDailyAnalysis).filter(
            LineDailyAnalysis.case_id == case_id,
            LineDailyAnalysis.analysis_date == target_date,
            LineDailyAnalysis.line_platform_group_id == source_identifier,
        ).first()
        if existing:
            skipped += 1
            continue
        case = db.query(Case).filter(Case.id == case_id).first()
        if not case:
            skipped += 1
            continue
        source_link = db.query(LineSourceLink).filter(LineSourceLink.source_id == source_identifier).first() if source_identifier else None
        if source_link and source_link.category != LineSourceCategory.case_family:
            skipped += 1
            continue
        raw_text = "\n".join(
            f"{message.sender_name or message.line_user_id or 'LINE'}：{message.message_text or '[' + message.message_type + ']'}"
            for message in group_messages
        )
        contact_record, complaint = create_contact_record_from_analysis(
            db,
            case,
            actor,
            raw_text,
            target_date,
            contact_type=_contact_type_from_source(source_link),
        )
        db.add(LineDailyAnalysis(
            case_id=case.id,
            line_platform_group_id=source_identifier,
            analysis_date=target_date,
            message_count=len(group_messages),
            contact_record_id=contact_record.id,
            complaint_id=complaint.id if complaint else None,
            note=f"每日批次由 {len(group_messages)} 則 LINE 訊息產生。來源：{source_identifier or '未記錄'}",
        ))
        created += 1

    return DailyAnalysisResult(created=created, skipped=skipped, message_groups=len(grouped))
