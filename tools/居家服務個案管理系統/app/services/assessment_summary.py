"""Readable, rule-based narratives for assessment records.

The text restates information already recorded in an assessment.  It is a
reading aid for the supervisor, not a diagnosis or an automated care decision.
"""

from app.models.assessment import Assessment


def build_assessment_summary(assessment: Assessment) -> str:
    items = {item.item_code: item for item in assessment.items}

    def value(code: str) -> str | None:
        item = items.get(code)
        return item.text_value if item else None

    def note(code: str) -> str | None:
        item = items.get(code)
        return item.note if item else None

    dependency = value("dependency_level") or "未計算"
    adl = assessment.adl_total_score if assessment.adl_total_score is not None else 0
    iadl = assessment.iadl_total_score if assessment.iadl_total_score is not None else 0
    if dependency in {"獨立", "輕度依賴"}:
        function_phrase = "日常生活大致仍保有自主能力，必要時可提供提醒或部分協助"
    elif dependency in {"中度依賴", "中重度依賴"}:
        function_phrase = "部分日常活動及工具性活動已有明顯協助需求，照顧安排宜兼顧安全與其可自行完成的能力"
    else:
        function_phrase = "日常生活多仰賴他人協助，照顧服務應優先維持安全、舒適與基本生活需求"

    outing = ""
    if assessment.outing_frequency is not None:
        if assessment.outing_frequency == 0:
            outing = "目前未規律外出，"
        elif assessment.outing_frequency < 2:
            outing = f"平均每週外出約 {assessment.outing_frequency:g} 次，"
        else:
            outing = f"平均每週外出約 {assessment.outing_frequency:g} 次，仍維持一定社區參與，"
    paragraphs = [
        f"從本次評估來看，個案 {function_phrase}。ADL 為 {adl}/100、IADL 為 {iadl}/100，系統依量表換算為「{dependency}」；{outing}後續可依實際表現持續調整協助強度。"
    ]

    cognition = []
    orientation = value("COG_orientation")
    expression = value("COG_expression")
    emotion = value("COG_emotion")
    behavior = value("COG_behavior")
    if orientation:
        cognition.append(f"意識與定向感為{orientation}")
    if expression:
        cognition.append(f"表達與理解能力{expression}")
    if emotion and emotion != "從未":
        cognition.append(f"情緒上有{emotion}的憂鬱或焦慮徵兆")
    if behavior and behavior != "未出現":
        cognition.append(f"異常行為{behavior}")
    if cognition:
        paragraphs.append("在認知與情緒方面，目前" + "，".join(cognition) + "；服務過程中可持續觀察情緒、溝通與行為變化。")

    burden = value("FAM_burden_level")
    support = value("FAM_support")
    cohabit = value("FAM_cohabit_change")
    family_bits = []
    if burden:
        if burden == "輕度負荷":
            family_bits.append("照顧者目前負荷較輕")
        else:
            family_bits.append(f"照顧者負荷評估為{burden}，需留意照顧壓力累積")
    if support:
        family_bits.append(f"家庭支持資源為{support}")
    if cohabit == "是":
        family_bits.append("同住狀況已有變動")
    if family_bits:
        paragraphs.append("就家庭照顧情形而言，" + "，".join(family_bits) + "。建議與主要照顧者持續確認可提供的協助及喘息需求。")

    risk = value("ENV_risk_level")
    risk_detail = note("ENV_risk_level")
    device_need = value("ENV_assistive_device")
    if risk or device_need:
        environment = "居住環境部分，"
        if risk:
            if risk == "低風險":
                environment += "目前整體風險較低"
            else:
                environment += f"評估為{risk}，建議優先檢視動線、照明與防滑等安全措施"
            if risk_detail:
                environment += f"（{risk_detail}）"
        if device_need == "是":
            environment += "；另有輔具新增或調整需求，可再評估合適的輔具與使用方式"
        paragraphs.append(environment + "。")

    satisfaction = value("ECON_satisfaction")
    referral = value("ECON_referral")
    language = value("CUL_language")
    communication = value("CUL_comm_assist")
    service_bits = []
    if satisfaction:
        service_bits.append(f"目前對服務的滿意度為{satisfaction}")
    if referral == "是":
        service_bits.append("有福利資源轉介需求，宜進一步確認可連結的資源")
    if language:
        language_text = f"溝通時以{language}為主"
        if communication == "是":
            language_text += "，並需要適度協助"
        service_bits.append(language_text)
    if service_bits:
        paragraphs.append("服務與溝通安排上，" + "；".join(service_bits) + "。")

    concerns = []
    if dependency not in {"獨立", "輕度依賴"}:
        concerns.append("生活功能維持")
    if burden in {"中度負荷", "重度負荷"}:
        concerns.append("照顧者支持")
    if risk in {"中風險", "高風險"}:
        concerns.append("居家安全")
    if emotion in {"偶爾", "經常"} or behavior in {"偶爾出現", "經常出現"}:
        concerns.append("情緒與行為觀察")
    if concerns:
        paragraphs.append("整體而言，後續服務可優先聚焦於" + "、".join(concerns) + "，並於後續評估時追蹤其變化。")
    else:
        paragraphs.append("整體狀況目前相對穩定，建議依既有照顧計畫持續服務，並於後續評估時追蹤生活功能與支持需求的變化。")

    return "\n\n".join(paragraphs)
