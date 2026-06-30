from app.models.assessment import RecordStatus
from app.services.record_workflow import active_approval_logs


ELECTRONIC_STAMPS = {
    "陳燕惠": {
        "title": "主任",
        "image_url": "/static/stamps/director_chen_yan_hui.svg",
    },
    "童樂欣": {
        "title": "居服督導",
        "image_url": "/static/stamps/supervisor_tong_le_xin.svg",
    },
    "陳梅馨": {
        "title": "居督督導",
        "image_url": "/static/stamps/supervisor_chen_mei_xin.svg",
    },
}

DEFAULT_REVIEW_NOTES = {
    "主責居督核閱",
    "電訪紀錄核閱通過",
    "核閱通過",
}


def stamp_for_user(user):
    if not user:
        return None
    return ELECTRONIC_STAMPS.get(user.display_name)


def review_rows(logs, active_only: bool = True):
    source_logs = active_approval_logs(logs) if active_only else logs
    rows = []
    for log in source_logs:
        user = log.changed_by_user
        stamp = stamp_for_user(user)
        note = log.change_note or ""
        rows.append({
            "created_at": log.created_at,
            "name": user.display_name if user else "-",
            "role": stamp["title"] if stamp else (user.role.value if user and user.role else "-"),
            "status": "已決行" if log.to_status == RecordStatus.approved.value else "已核章",
            "note": "" if note in DEFAULT_REVIEW_NOTES else note,
            "stamp": stamp,
        })
    return rows


def contact_review_rows(logs):
    return review_rows(logs, active_only=True)
