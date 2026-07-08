"""長照給付服務代碼（BA/GA/SC）清單，含每單位所需分鐘數。
分鐘數依機構實務慣用標準整理（非衛福部給付基準原文逐字標示，
官方支付基準多數BA碼僅以「組合」計價，未逐項標示分鐘數；
分鐘數供本系統計算照顧計畫服務所需時間使用）。"""

BA_CODES = [
    ("BA01", "基本身體清潔", 30),
    ("BA02", "基本日常照顧", 30),
    ("BA03", "測量生命徵象", 10),
    ("BA04", "協助進食或管灌餵食", 20),
    ("BA05-1", "餐食照顧（一般備餐）", 40),
    ("BA07", "協助沐浴及洗頭", 30),
    ("BA10", "翻身拍背", 20),
    ("BA11", "肢體關節活動", 20),
    ("BA12", "協助上下樓梯", 10),
    ("BA13", "陪同外出", 30),
    ("BA14", "陪同就醫", 90),
    ("BA15-1", "家務協助（獨居）", 30),
    ("BA15-2", "家務協助（非獨居）", 30),
    ("BA16-1", "代購或代領或代送服務（自用）", 15),
    ("BA16-2", "代購或代領或代送服務（共用）", 15),
    ("BA17a", "協助執行輔助性醫療－人工氣道內分泌物抽吸", 10),
    ("BA17b", "協助執行輔助性醫療－口腔內懸壅垂之前分泌物抽吸", 10),
    ("BA17c", "協助執行輔助性醫療－尿管及鼻胃管之清潔與固定", 10),
    ("BA17d1", "協助執行輔助性醫療－血糖機驗血糖", 10),
    ("BA17d2", "協助執行輔助性醫療－甘油球通便", 15),
    ("BA17e", "協助執行輔助性醫療－依指示置入藥盒", 10),
    ("BA18", "安全看視", 30),
    ("BA20", "陪伴服務", 30),
    ("BA22", "巡視服務", 15),
    ("BA23", "協助洗頭", 20),
    ("BA24", "協助排泄", 20),
]

GA_SC_CODES = [
    ("GA09", "居家喘息", 120),
    ("SC09", "居家短照", 120),
]

ZA_CODES = [
    ("ZA04", "服務未遇（半小時工時）", 30),
]

ALL_CODES = BA_CODES + GA_SC_CODES + ZA_CODES
CODE_LOOKUP = {code: (name, minutes) for code, name, minutes in ALL_CODES}

# 對服務對象收費金額（每單位）
CHARGE_AMOUNTS: dict[str, int] = {
    "ZA04": 100,
}


def parse_funding(codes: str | None, default_funding: str, funding_detail_json: str | None) -> dict[str, str]:
    """回傳 {service_code: funding_source} 對照表。
    支援同碼別混合補助/自費：funding_detail 中 value 可為陣列
    `[{"funding":"自費","qty":1},{"funding":"補助","qty":1}]`，
    回傳鍵值為 `{code}.{index}` 格式。

    Args:
        codes: 原始 service_codes 字串，例如 "BA07x1, BA20x3"
        default_funding: 預設值（funding_source 欄位）
        funding_detail_json: funding_detail JSON 字串
    Returns:
        dict 例如 {"BA07": "自費", "BA20": "補助"} 或
        {"BA07": "自費", "BA20.0": "自費", "BA20.1": "補助"}
    """
    import json, re
    funding_detail_raw: dict = {}
    if funding_detail_json:
        try:
            funding_detail_raw = json.loads(funding_detail_json)
        except (json.JSONDecodeError, TypeError):
            pass

    codes_present: set[str] = set()
    if codes:
        for m in re.finditer(r"([A-Z]{1,3}\d{1,2}(?:-\d+)?(?:[a-z]\d?)?)\s*x\s*(\d+)", codes, re.IGNORECASE):
            codes_present.add(m.group(1).upper())

    result: dict[str, str] = {}
    for code in sorted(codes_present):
        val = funding_detail_raw.get(code, default_funding)
        if isinstance(val, list):
            idx = 0
            for g in val:
                qty = g.get("qty", 1)
                fund_val = g.get("funding", default_funding)
                for _ in range(qty):
                    result[f"{code}.{idx}"] = fund_val
                    idx += 1
        elif isinstance(val, str):
            result[code] = val
        else:
            result[code] = default_funding
    return result if result else {"*": default_funding}


def collapse_funding_groups(funding_map: dict[str, str]) -> str | None:
    """將 parse_funding 回傳的 dict 壓回 JSON 字串供存入資料庫。
    若全部為預設值則回傳 None。
    """
    import json
    simple: dict[str, str | list] = {}
    groups: dict[str, list[dict]] = {}
    for key, val in sorted(funding_map.items()):
        if "." in key:
            code, idx = key.rsplit(".", 1)
            groups.setdefault(code, []).append({"funding": val, "qty": 1})
        else:
            simple[key] = val
    for code, items in groups.items():
        simple[code] = items
    # 清理：如果只有一個碼且值與 default 相同，視情況簡化
    # 此處直接回傳 JSON 給呼叫方決定是否簡化
    return json.dumps(simple, ensure_ascii=False) if any(v != "補助" for v in funding_map.values()) else None


def get_code_quantity(codes: str | None, code_target: str) -> int:
    """從 service_codes 字串中取得指定碼別的總數量。"""
    import re
    total = 0
    if codes:
        for m in re.finditer(r"([A-Z]{1,3}\d{1,2}(?:-\d+)?(?:[a-z]\d?)?)\s*x\s*(\d+)", codes, re.IGNORECASE):
            if m.group(1).upper() == code_target.upper():
                total += int(m.group(2))
    return total
