import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

os.makedirs("data", exist_ok=True)

engine = create_engine(
    settings.database_url, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def apply_compatible_schema_updates():
    """補齊 SQLite 舊資料庫缺少的欄位；不重建、也不刪除既有資料。"""
    with engine.begin() as connection:
        inspector = inspect(connection)
        for table_name, column_name in [
            ("goals", "origin_assessment_id"),
            ("goals", "predecessor_goal_id"),
            ("care_plans", "origin_assessment_id"),
            ("care_plans", "predecessor_care_plan_id"),
            ("line_messages", "sender_name"),
            ("users", "employee_no"),
            ("users", "id_number"),
            ("users", "gender"),
            ("users", "birth_date"),
            ("users", "phone"),
            ("users", "mobile"),
            ("users", "email"),
            ("users", "address"),
            ("users", "job_title"),
            ("users", "employment_status"),
            ("users", "hire_date"),
            ("users", "termination_date"),
            ("users", "supervisor_id"),
            ("users", "languages"),
            ("users", "emergency_contact_name"),
            ("users", "emergency_contact_relation"),
            ("users", "emergency_contact_phone"),
            ("users", "note"),
            ("users", "must_change_password"),
            ("users", "regular_off_weekday"),
            ("users", "rest_weekday"),
            ("users", "hourly_wage"),
            ("caregiver_service_records", "formalization_status"),
            ("contact_records", "phone_call_type"),
            ("contact_records", "followup_required"),
            ("contact_records", "followup_note"),
            ("contact_records", "followup_completed_at"),
            ("contact_records", "followup_completed_by"),
            ("complaint_reports", "report_kind"),
            ("complaint_reports", "final_result_due_date"),
            ("complaint_reports", "initial_record_content"),
            ("complaint_reports", "initial_record_submitted_by"),
            ("complaint_reports", "initial_record_approved_at"),
            ("complaint_reports", "initial_record_approved_by"),
            ("complaint_reports", "initial_record_returned_at"),
            ("complaint_reports", "initial_record_return_note"),
            ("complaint_reports", "incident_date"),
            ("complaint_reports", "incident_location"),
            ("complaint_reports", "accused_name"),
            ("complaint_reports", "accused_relationship"),
            ("complaint_reports", "witness_info"),
            ("complaint_reports", "requested_support"),
            ("complaint_reports", "final_result_content"),
            ("complaint_reports", "final_result_submitted_at"),
            ("complaint_reports", "final_result_submitted_by"),
            ("complaint_reports", "final_result_approved_at"),
            ("complaint_reports", "final_result_approved_by"),
            ("complaint_reports", "final_result_returned_at"),
            ("complaint_reports", "final_result_return_note"),
            ("complaint_reports", "reply_submitted_at"),
            ("complaint_reports", "reply_submitted_by"),
            ("complaint_reports", "reply_approved_at"),
            ("complaint_reports", "reply_approved_by"),
            ("complaint_reports", "reply_returned_at"),
            ("complaint_reports", "reply_return_note"),
            ("complaint_reports", "reply_read_at"),
        ]:
            if table_name in inspector.get_table_names():
                columns = {column["name"] for column in inspector.get_columns(table_name)}
                if column_name not in columns:
                    connection.execute(text(
                        f"ALTER TABLE {table_name} ADD COLUMN {column_name} VARCHAR"
                    ))
        if "contact_records" in inspector.get_table_names():
            columns = {column["name"] for column in inspector.get_columns("contact_records")}
            if "followup_required" in columns:
                connection.execute(text("""
                    UPDATE contact_records
                    SET followup_required = 0
                    WHERE followup_required IS NULL OR followup_required = ''
                """))
        if "complaint_reports" in inspector.get_table_names():
            columns = {column["name"] for column in inspector.get_columns("complaint_reports")}
            if "report_kind" in columns:
                connection.execute(text("""
                    UPDATE complaint_reports
                    SET report_kind = 'general'
                    WHERE report_kind IS NULL OR report_kind = ''
                """))
            if "final_result_due_date" in columns:
                connection.execute(text("""
                    UPDATE complaint_reports
                    SET final_result_due_date = initial_record_due_date
                    WHERE final_result_due_date IS NULL OR final_result_due_date = ''
                """))
        if "users" in inspector.get_table_names():
            columns = {column["name"] for column in inspector.get_columns("users")}
            if "must_change_password" in columns:
                connection.execute(text("""
                    UPDATE users
                    SET must_change_password = NULL
                    WHERE must_change_password = '0' OR must_change_password = 0
                """))
        if "caregiver_service_records" in inspector.get_table_names():
            columns = {column["name"] for column in inspector.get_columns("caregiver_service_records")}
            if "formalization_status" in columns:
                connection.execute(text("""
                    UPDATE caregiver_service_records
                    SET formalization_status = 'external_import'
                    WHERE formalization_status IS NULL OR formalization_status = ''
            """))
        if "cases" in inspector.get_table_names():
            columns = {c["name"] for c in inspector.get_columns("cases")}
            for col in ("is_dialysis", "dialysis_hospital_address", "dialysis_direction"):
                if col not in columns:
                    connection.execute(text(f"ALTER TABLE cases ADD COLUMN {col} VARCHAR"))
        if "aa_import_raw_records" in inspector.get_table_names():
            columns = {c["name"] for c in inspector.get_columns("aa_import_raw_records")}
            if "personnel" not in columns:
                connection.execute(text("ALTER TABLE aa_import_raw_records ADD COLUMN personnel VARCHAR"))
        if "import_salary_records" not in inspector.get_table_names():
            from app.models.import_salary_record import ImportSalaryRecord
            ImportSalaryRecord.__table__.create(connection)
        else:
            isr_cols = {c["name"] for c in inspector.get_columns("import_salary_records")}
            for col in ("visit_order", "transfer_minutes", "weighted_total"):
                if col not in isr_cols:
                    col_type = "INTEGER" if col == "visit_order" else "FLOAT"
                    connection.execute(text(f"ALTER TABLE import_salary_records ADD COLUMN {col} {col_type}"))
        # 保險應扣欄位
        if "monthly_salaries" in inspector.get_table_names():
            ms_cols = {c["name"] for c in inspector.get_columns("monthly_salaries")}
            for col in ("labor_insurance_deduction", "health_insurance_deduction", "labor_pension_deduction"):
                if col not in ms_cols:
                    connection.execute(text(f"ALTER TABLE monthly_salaries ADD COLUMN {col} INTEGER DEFAULT 0"))
        # 健保眷屬加保表
        if "nhi_dependents" not in inspector.get_table_names():
            from app.models.nhi_dependent import NhiDependent
            NhiDependent.__table__.create(connection)
        else:
            nhi_cols = {c["name"] for c in inspector.get_columns("nhi_dependents")}
            for col in ("nationality", "is_child", "has_exemption", "subsidy_rate", "max_subsidy_amount"):
                if col not in nhi_cols:
                    if col in ("subsidy_rate", "max_subsidy_amount"):
                        connection.execute(text(f"ALTER TABLE nhi_dependents ADD COLUMN {col} INTEGER DEFAULT 0"))
                    elif col in ("is_child", "has_exemption"):
                        connection.execute(text(f"ALTER TABLE nhi_dependents ADD COLUMN {col} BOOLEAN DEFAULT 0"))
                    else:
                        connection.execute(text(f"ALTER TABLE nhi_dependents ADD COLUMN {col} VARCHAR DEFAULT '本國人'"))
        # 勞健保級距欄位
        if "users" in inspector.get_table_names():
            ucols = {c["name"] for c in inspector.get_columns("users")}
            for col in ("insurance_labor_amount", "insurance_occupational_amount",
                        "insurance_labor_pension_amount", "labor_pension_employer_rate",
                        "labor_pension_personal_rate", "insurance_health_amount",
                        "health_dependents", "has_exemption", "subsidy_rate", "insurance_note",
                        "insurance_effective_year", "insurance_effective_month"):
                if col not in ucols:
                    if col == "labor_pension_employer_rate":
                        default_val = "6"
                    elif col == "has_exemption":
                        connection.execute(text(f"ALTER TABLE users ADD COLUMN {col} BOOLEAN DEFAULT 0"))
                        continue
                    elif col == "insurance_note":
                        connection.execute(text(f"ALTER TABLE users ADD COLUMN {col} VARCHAR"))
                        continue
                    else:
                        default_val = "0"
                    connection.execute(text(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT {default_val}"))
        # 本功能上線前建立的目標／計畫沒有評估來源；依實務將它們歸回個案的初次評估，
        # 讓既有資料在新的評估分組畫面仍可被找到與檢討。
        for table_name in ("goals", "care_plans"):
            if table_name in inspector.get_table_names() and "assessments" in inspector.get_table_names():
                connection.execute(text(f"""
                    UPDATE {table_name}
                    SET origin_assessment_id = (
                        SELECT assessments.id FROM assessments
                        WHERE assessments.case_id = {table_name}.case_id
                        ORDER BY assessments.assessment_date ASC
                        LIMIT 1
                    )
                    WHERE origin_assessment_id IS NULL
                    AND EXISTS (
                        SELECT 1 FROM assessments WHERE assessments.case_id = {table_name}.case_id
                    )
                """))
        # 照顧計畫若已連結目標，應以該目標的評估來源為準；修復舊版建立時漏存來源的資料。
        if all(name in inspector.get_table_names() for name in ("care_plans", "care_plan_goals", "goals")):
            connection.execute(text("""
                UPDATE care_plans
                SET origin_assessment_id = (
                    SELECT goals.origin_assessment_id
                    FROM care_plan_goals
                    JOIN goals ON goals.id = care_plan_goals.goal_id
                    WHERE care_plan_goals.care_plan_id = care_plans.id
                    AND goals.origin_assessment_id IS NOT NULL
                    LIMIT 1
                )
                WHERE origin_assessment_id IS NULL
                AND EXISTS (
                    SELECT 1 FROM care_plan_goals
                    JOIN goals ON goals.id = care_plan_goals.goal_id
                    WHERE care_plan_goals.care_plan_id = care_plans.id
                    AND goals.origin_assessment_id IS NOT NULL
                )
            """))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
