"""端到端測試：三種申訴流程的簽核層級"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

import requests
from app.database import SessionLocal
from app.models.complaint_report import ComplaintReport, ComplaintReportKind
from app.models.record_status_log import RecordStatusLog

BASE = "http://localhost:8001"
PW = "test1234"

session = requests.Session()

def login(username):
    r = session.post(f"{BASE}/login", data={"username": username, "password": PW}, allow_redirects=False)
    assert r.status_code == 302, f"Login failed for {username}: {r.status_code}"
    print(f"  ✓ Login as {username}")

def get(path):
    r = session.get(f"{BASE}{path}")
    print(f"  GET {path} => {r.status_code}")
    return r

def post(path, data):
    r = session.post(f"{BASE}{path}", data=data, allow_redirects=False)
    print(f"  POST {path} => {r.status_code}")
    if r.status_code == 302:
        loc = r.headers.get("Location", "")
        # Extract report_id from location
        if "created=" in loc:
            rid = loc.split("created=")[1].split("&")[0]
            return rid
        if "/complaints/" in loc:
            return loc.split("/complaints/")[1].split("?")[0].split("/")[0]
    return None

def check_logs(report_id, label):
    db = SessionLocal()
    logs = db.query(RecordStatusLog).filter(
        RecordStatusLog.record_type == "complaint_report",
        RecordStatusLog.record_id == report_id,
    ).order_by(RecordStatusLog.created_at.asc()).all()
    print(f"  --- {label} 簽核紀錄 ({len(logs)} 筆) ---")
    for log in logs:
        user = log.changed_by_user
        uname = user.display_name if user else "?"
        print(f"    {log.created_at.strftime('%H:%M:%S')} | {uname} | {log.to_status} | {log.change_note or ''}")
    print()
    db.close()

def get_report(report_id):
    from sqlalchemy.orm import joinedload
    db = SessionLocal()
    r = db.query(ComplaintReport).options(
        joinedload(ComplaintReport.assigned_reviewer),
        joinedload(ComplaintReport.responsible_user),
        joinedload(ComplaintReport.initial_record_author),
        joinedload(ComplaintReport.initial_record_approver),
        joinedload(ComplaintReport.final_result_author),
        joinedload(ComplaintReport.final_result_approver),
        joinedload(ComplaintReport.reply_author),
        joinedload(ComplaintReport.reply_approver),
        joinedload(ComplaintReport.submitted_by),
    ).filter(ComplaintReport.id == report_id).first()
    # force load all attributes before closing
    if r:
        _ = r.assigned_reviewer
        _ = r.responsible_user
        _ = r.initial_record_author
        _ = r.initial_record_approver
        _ = r.final_result_author
        _ = r.final_result_approver
        _ = r.reply_author
        _ = r.reply_approver
        _ = r.submitted_by
    db.close()
    return r

def get_case_ids():
    db = SessionLocal()
    from app.models.case import Case, CaseStatus
    c = db.query(Case).filter(Case.status == CaseStatus.active).first()
    db.close()
    return c.id if c else None

# ============================================================
# 測試流程
# ============================================================
case_id = get_case_ids()
print(f"Using case_id: {case_id}")

if not case_id:
    print("No active case found, creating one first...")
    login("admin")
    r = get("/cases/new")
    if r.status_code != 200:
        print("Cannot create case, aborting")
        sys.exit(1)

# ============================================================
# TEST 1: 一般申訴（員工本人→直屬主管）
# ============================================================
print("\n" + "="*60)
print("【測試1】一般申訴 — 居服員李郁萱 → 直屬主管陳梅馨 → 主任陳燕惠")
print("="*60)

login("W063")  # 李郁萱 (居服員)

# Submit general complaint
rid1 = post("/complaints/new", {
    "report_kind": "一般申訴",
    "reporter_type": "員工本人申訴",
    "submit_to": "supervisor",
    "employee_category": "管理溝通",
    "subject": "測試：班表安排不公",
    "content": "這個月的班表我只有15天，但其他同事都有20天以上。跟督導反映後沒有下文。",
    "expected_resolution": "希望重新檢討班表安排方式",
})
if not rid1:
    print("  ✗ Failed to submit complaint, checking...")
    r = session.get(f"{BASE}/complaints/new")
    if "error" in r.text:
        import re
        m = re.search(r'class="error"[^>]*>([^<]+)', r.text)
        print(f"  Error: {m.group(1) if m else 'unknown'}")
    sys.exit(1)

r1 = get_report(rid1)
print(f"  申訴單ID: {rid1[:12]}")
print(f"  狀態: {r1.status.value}")
print(f"  審核者: {r1.assigned_reviewer.display_name if r1.assigned_reviewer else 'None'}")
print(f"  主責人員: {r1.responsible_user.display_name if r1.responsible_user else 'None'}")
print(f"  期限: 初記={r1.initial_record_due_date}, 最終={r1.final_result_due_date}")
check_logs(rid1, "送出申訴")

# Step 2: 陳梅馨 (主管) 送出初次處理紀錄
print("\n--- Step 2: 陳梅馨送出初次處理紀錄 ---")
login("W012")  # 陳梅馨 (主管)
r = get(f"/complaints/{rid1}")
assert r.status_code == 200

post(f"/complaints/{rid1}/initial-record", {
    "content": "已與李郁萱面談了解情況。六月班表因李員臨時提出6/10-6/14休假需求，故排班天數減少。已同意七月優先補班。"
})
r1 = get_report(rid1)
print(f"  狀態: {r1.status.value}")
check_logs(rid1, "初次處理紀錄送出")

# Step 3: 陳燕惠 (主任) 核章初次處理紀錄
print("\n--- Step 3: 陳燕惠主任核章初次處理紀錄 ---")
login("W002")  # 陳燕惠 (主任)
# Check: can_approve_initial should be True, can_return_initial should be True
# The approval button should show "主任決行首次處理紀錄"
r = get(f"/complaints/{rid1}")
assert r.status_code == 200
if "主任決行首次處理紀錄" in r.text or "主任決行" in r.text:
    print("  ✓ 主任決行按鈕出現")
else:
    print("  ⚠ 按鈕文字檢查：主任決行按鈕可能不存在")
    print(f"  (找: 主任決行, 有={('主任決行' in r.text)}; 找: final, 有={('final' in r.text)}")

post(f"/complaints/{rid1}/initial-record/approve", {"note": "了解，請依協調結果調整"})
r1 = get_report(rid1)
print(f"  初記核准時間: {r1.initial_record_approved_at}")
print(f"  初記核准人: {r1.initial_record_approver.display_name if r1.initial_record_approver else 'None'}")
check_logs(rid1, "初次處理紀錄核章")

# Step 4: 陳梅馨送出最終處理結果
print("\n--- Step 4: 陳梅馨送出最終處理結果 ---")
login("W012")
post(f"/complaints/{rid1}/final-result", {
    "content": "處理結果：與李郁萱達成共識，七月班表將優先安排至少20天服務日；未來排班若有特殊休假需求，需於每月20日前提出。申訴人表示接受。"
})
r1 = get_report(rid1)
print(f"  狀態: {r1.status.value}")
check_logs(rid1, "最終處理結果送出")

# Step 5: 陳燕惠核章最終處理結果
print("\n--- Step 5: 陳燕惠主任核章最終處理結果 ---")
login("W002")
post(f"/complaints/{rid1}/final-result/approve", {"note": "同意處理方式"})
r1 = get_report(rid1)
print(f"  最終核准時間: {r1.final_result_approved_at}")
print(f"  最終核准人: {r1.final_result_approver.display_name if r1.final_result_approver else 'None'}")
check_logs(rid1, "最終處理結果核章")

# Step 6: 陳梅馨送出回覆紀錄
print("\n--- Step 6: 陳梅馨送出回覆申訴人紀錄 ---")
login("W012")
post(f"/complaints/{rid1}/reply", {
    "content": "李郁萱君您好：\n有關您反映的班表安排事宜，經與您面談了解，六月因您臨時提出休假需求致排班天數減少，已協調七月優先補班。日後排班若有特殊需求請於每月20日前提出，以利安排。\n如您對處理結果有疑問，請再與主管聯繫。謝謝。"
})
r1 = get_report(rid1)
print(f"  狀態: {r1.status.value}")
check_logs(rid1, "回覆紀錄送出")

# Step 7: 陳燕惠核章回覆紀錄
print("\n--- Step 7: 陳燕惠主任核章回覆紀錄 → 結案 ---")
login("W002")
post(f"/complaints/{rid1}/reply/approve", {"note": "回覆適當，准予結案"})
r1 = get_report(rid1)
print(f"  狀態: {r1.status.value}")
print(f"  結案時間: {r1.closed_at}")
print(f"  回覆核準時間: {r1.reply_approved_at}")
check_logs(rid1, "回覆紀錄核章")

# ============================================================
# TEST 2: 性騷擾申訴
# ============================================================
print("\n" + "="*60)
print("【測試2】性騷擾申訴 — 居服員李郁萱 → 直屬主管陳梅馨")
print("="*60)

login("W063")
rid2 = post("/complaints/new", {
    "report_kind": "性騷擾申訴",
    "reporter_type": "員工本人申訴",
    "submit_to": "supervisor",
    "employee_category": "職場互動",
        "subject": "測試：同事言語騷擾",
        "content": '某男性同仁多次在休息時間對我說"妳今天穿這樣很好看"等不當言語，經制止後仍未改善。',
    "incident_date": "2026-06-28",
    "incident_location": "辦公室休息區",
    "accused_name": "陳○○",
    "accused_relationship": "同事",
    "witness_info": "其他在場同事",
    "requested_support": "希望調整排班避免同班",
})
r2 = get_report(rid2)
print(f"  申訴單ID: {rid2[:12]}")
print(f"  狀態: {r2.status.value}")
print(f"  審核者: {r2.assigned_reviewer.display_name if r2.assigned_reviewer else 'None'}")
print(f"  最終期限: {r2.final_result_due_date}")
check_logs(rid2, "送出性騷擾申訴")

# 陳梅馨送出初次處理紀錄
print("\n--- Step 2: 陳梅馨送出初次處理紀錄 ---")
login("W012")
post(f"/complaints/{rid2}/initial-record", {
    "content": "已個別約談申訴人與被申訴人了解情況。申訴人表示希望調整班表避免同班，被申訴人表示無惡意但會注意言行。將安排性騷擾防治教育訓練，並調整七月班表。"
})
check_logs(rid2, "初記送出")

# 陳燕惠核章
print("\n--- Step 3: 陳燕惠主任核章 ---")
login("W002")
post(f"/complaints/{rid2}/initial-record/approve", {"note": "請依程序辦理，注意保密"})
check_logs(rid2, "初記核章")

# 陳梅馨送出最終處理結果
print("\n--- Step 4: 陳梅馨送出最終處理結果 ---")
login("W012")
post(f"/complaints/{rid2}/final-result", {
    "content": "處理結果：1. 已安排被申訴人參與6/30性騷擾防治教育訓練。2. 七月起調整班表，兩人不再同天值班。3. 申訴人表示接受。"
})
check_logs(rid2, "最終結果送出")

# 陳燕惠核章
print("\n--- Step 5: 陳燕惠主任核章最終處理結果 ---")
login("W002")
post(f"/complaints/{rid2}/final-result/approve", {"note": "同意"})
r2 = get_report(rid2)
print(f"  最終核准時間: {r2.final_result_approved_at}")
check_logs(rid2, "最終結果核章")

# 陳梅馨送出回覆
print("\n--- Step 6: 陳梅馨送出回覆 ---")
login("W012")
post(f"/complaints/{rid2}/reply", {
    "content": "李員您好，有關您反映之職場互動問題，已依性騷擾防治相關規定處理完畢，詳情請與主管聯繫。"
})
check_logs(rid2, "回覆送出")

# 陳燕惠核章回覆
print("\n--- Step 7: 陳燕惠核章回覆 → 結案 ---")
login("W002")
post(f"/complaints/{rid2}/reply/approve", {"note": "准予結案"})
r2 = get_report(rid2)
print(f"  狀態: {r2.status.value}")
print(f"  結案時間: {r2.closed_at}")
check_logs(rid2, "回覆核章結案")

# ============================================================
# TEST 3: 性侵害申訴
# ============================================================
print("\n" + "="*60)
print("【測試3】性侵害申訴 — 居服員李郁萱 → 直屬主管陳梅馨")
print("="*60)

login("W063")
rid3 = post("/complaints/new", {
    "report_kind": "性侵害申訴",
    "reporter_type": "員工本人申訴",
    "submit_to": "supervisor",
    "employee_category": "工作安全",
    "subject": "測試：服務過程中遭案家肢體碰觸",
    "content": "今日至案家服務時，案主配偶趁協助移位時觸碰臀部，已明確制止。",
    "incident_date": "2026-06-29",
    "incident_location": "案家住所",
    "accused_name": "案主配偶",
    "accused_relationship": "服務對象家屬",
    "witness_info": "無",
    "requested_support": "暫停該案服務，改由其他居服員接手",
})
r3 = get_report(rid3)
print(f"  申訴單ID: {rid3[:12]}")
print(f"  狀態: {r3.status.value}")
print(f"  審核者: {r3.assigned_reviewer.display_name if r3.assigned_reviewer else 'None'}")
print(f"  最終期限: {r3.final_result_due_date}")
check_logs(rid3, "送出性侵害申訴")

# 陳梅馨送出初次處理紀錄
print("\n--- Step 2: 陳梅馨送出初次處理紀錄 ---")
login("W012")
post(f"/complaints/{rid3}/initial-record", {
    "content": "已與申訴人面談了解案發情況。已暫停該案服務，改由其他居服員接班。已告知申訴人如有需要可協助報警。申訴人表示暫不報警，先觀察。"
})
check_logs(rid3, "初記送出")

# 陳燕惠核章
print("\n--- Step 3: 陳燕惠主任核章 ---")
login("W002")
post(f"/complaints/{rid3}/initial-record/approve", {"note": "已了解，請持續追蹤"})
check_logs(rid3, "初記核章")

# 陳梅馨送出結案紀錄（性侵害專用 = final_result 但不走 reply）
print("\n--- Step 4: 陳梅馨送出結案紀錄陳核 ---")
login("W012")
post(f"/complaints/{rid3}/final-result", {
    "content": "結案紀錄：1. 該案已改由女性居服員接班服務。2. 已向案家明確告知服務規範。3. 申訴人情緒穩定，表示暫不報警，但已告知後續如需協助可隨時提出。4. 主管將於一週後再追蹤確認。"
})
check_logs(rid3, "結案紀錄送出")

# 陳燕惠核章結案紀錄
print("\n--- Step 5: 陳燕惠主任核章結案紀錄 ---")
login("W002")
post(f"/complaints/{rid3}/final-result/approve", {"note": "同意結案，請持續追蹤一個月"})
r3 = get_report(rid3)
print(f"  狀態: {r3.status.value}")
print(f"  結案時間: {r3.closed_at}")
print(f"  注意：性侵害申訴應直接結案，不走回覆流程")
check_logs(rid3, "結案核章")

# ============================================================
# 最終報告
# ============================================================
print("\n" + "="*60)
print("最終報告：簽核層級彙整")
print("="*60)

for label, rid, expected_type in [
    ("一般申訴", rid1, "general"),
    ("性騷擾申訴", rid2, "sexual_harassment"),
    ("性侵害申訴", rid3, "sexual_assault"),
]:
    r = get_report(rid)
    print(f"\n--- {label} ({rid[:12]}) ---")
    print(f"  Final Status: {r.status.value}")
    print(f"  Submitted: {r.submitted_by.display_name}")
    print(f"  Reviewer: {r.assigned_reviewer.display_name if r.assigned_reviewer else 'None'}")
    print(f"  Handler: {r.responsible_user.display_name if r.responsible_user else 'None'}")
    
    print(f"  初記: submitted={r.initial_record_submitted_at is not None}")
    if r.initial_record_submitted_by:
        author = r.initial_record_author
        print(f"        author={author.display_name if author else '?'}({author.role.value if author else '?'})")
    print(f"        approved={r.initial_record_approved_at is not None}")
    if r.initial_record_approved_by:
        approver = r.initial_record_approver
        print(f"        approver={approver.display_name if approver else '?'}({approver.role.value if approver else '?'})")
    
    print(f"  最終: submitted={r.final_result_submitted_at is not None}")
    if r.final_result_submitted_by:
        fauthor = r.final_result_author
        print(f"        author={fauthor.display_name if fauthor else '?'}({fauthor.role.value if fauthor else '?'})")
    print(f"        approved={r.final_result_approved_at is not None}")
    if r.final_result_approved_by:
        fapprover = r.final_result_approver
        print(f"        approver={fapprover.display_name if fapprover else '?'}({fapprover.role.value if fapprover else '?'})")
    
    print(f"  回覆: submitted={r.reply_submitted_at is not None}")
    print(f"        approved={r.reply_approved_at is not None}")
    print(f"  結案: {r.closed_at}")

print("\n✅ 測試完成")
