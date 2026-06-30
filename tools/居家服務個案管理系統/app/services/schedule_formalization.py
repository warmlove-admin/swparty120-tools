from sqlalchemy.orm import Session

from app.models.care_plan import CarePlan
from app.models.caregiver_service_record import CaregiverServiceRecord


EXTERNAL_IMPORT = "external_import"
PENDING_FORMALIZATION = "pending_formalization"
FORMALIZED = "formalized"
REPLACED = "replaced"


STATUS_LABELS = {
    EXTERNAL_IMPORT: "外部匯入",
    PENDING_FORMALIZATION: "待轉正式",
    FORMALIZED: "已轉正式",
    REPLACED: "已取代",
}


def sync_case_external_schedule_status(db: Session, case_id: str) -> int:
    """Mark imported records as pending once a formal care plan exists."""
    has_care_plan = db.query(CarePlan.id).filter(CarePlan.case_id == case_id).first() is not None
    if not has_care_plan:
        return 0
    return (
        db.query(CaregiverServiceRecord)
        .filter(
            CaregiverServiceRecord.case_id == case_id,
            CaregiverServiceRecord.formalization_status == EXTERNAL_IMPORT,
        )
        .update(
            {CaregiverServiceRecord.formalization_status: PENDING_FORMALIZATION},
            synchronize_session=False,
        )
    )
