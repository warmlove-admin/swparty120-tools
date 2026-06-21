"""5.4 衛福部「照顧服務管理資訊平臺」HTML匯出檔解析。

匯出檔為固定格式的<th>標籤</th><td>值</td>表格，依5.4風險提醒：
若平台未來改版格式，此解析程式需同步更新；但格式跑掉時欄位會直接抓不到值
（顯示空白），居督檢查表單時容易察覺，不會誤植錯誤資料。
"""
import re
from datetime import date
from typing import Optional

from bs4 import BeautifulSoup

# 表格中的label -> 我們系統欄位名稱對照表（僅取第一次出現的值，
# 避免後面CMS評估區段中同名小標題覆蓋掉個案基本資料區段的值）
LABEL_MAP = {
    "個案姓名": "name",
    "個案身分證": "id_number",
    "個案生日": "birth_date",
    "個案電話": "phone",
    "性別": "gender",
    "目前居住狀況": "living_status",
    "戶籍地址": "household_address",
    "居住(通訊)地址": "residence_address",
    "長照福利身份": "ltc_welfare_status",
    "A5.居住地": "residence_district",
    "CMS等級": "cms_level",
    "A單位名稱": "a_unit_name",
    "A個管姓名": "case_manager_name",
    "聯絡電話": "case_manager_contact",
    # 主要聯絡人
    "聯絡人姓名": "contact_name",
    "聯絡人手機": "contact_phone",
    "聯絡人與需要服務者關係或身分": "contact_relation",
    # 主要照顧者
    "B1a.主要照顧者姓名": "caregiver_name",
    "主要照顧者身分證": "caregiver_id_number",
    "主要照顧者生日": "caregiver_birth_date",
    "B1b.與個案之關係": "caregiver_relation",
    "手機": "caregiver_phone",
}


def _roc_date_to_iso(text: str) -> Optional[str]:
    """民國年格式（如 052/10/07）轉西元年ISO日期字串"""
    match = re.search(r"(\d{2,3})/(\d{1,2})/(\d{1,2})", text)
    if not match:
        return None
    roc_year, month, day = match.groups()
    try:
        ad_year = int(roc_year) + 1911
        return date(ad_year, int(month), int(day)).isoformat()
    except ValueError:
        return None


def parse_ltc_html(html_content: bytes) -> dict:
    """解析HTML，回傳可直接帶入開案表單的欄位字典。
    只取每個label第一次出現的值，符合表單從上到下「個案基本資料」最先出現的順序。
    """
    soup = BeautifulSoup(html_content, "lxml")
    result: dict = {}

    for row in soup.find_all("tr"):
        ths = row.find_all("th")
        tds = row.find_all("td")
        for th, td in zip(ths, tds):
            # th內常有多行（如「個案姓名<br>傳統姓名」），只取第一行純文字當作label
            lines = [s.strip() for s in th.stripped_strings if s.strip()]
            label = next((line for line in lines if line not in ("＊",)), "")
            label = re.sub(r"^＊", "", label).strip()

            field = LABEL_MAP.get(label)
            if not field or field in result:
                continue

            value = td.get_text(separator=" ", strip=True)
            value = re.sub(r"\s+", " ", value).strip()
            if not value:
                continue

            if field in ("birth_date", "caregiver_birth_date"):
                result[field] = _roc_date_to_iso(value)
            elif field in ("id_number", "caregiver_id_number"):
                result[field] = _extract_id_number(value)
            elif field == "residence_district":
                result[field] = _clean_residence_district(value)
            else:
                result[field] = value

    return result


def _extract_id_number(text: str) -> str:
    """身分證字號欄位常夾雜checkbox標籤文字（如「馬偕計畫外籍人士」），
    僅取第一個身分證格式字串（含CMS平台對已驗證個資的遮罩 *）。"""
    match = re.search(r"[A-Z][12]\d{8}|[A-Z]\d{4}\*{5}", text)
    return match.group(0) if match else text.split(" ")[0]


def _clean_residence_district(text: str) -> str:
    """A5.居住地欄位常包含縣市重複描述（如行政區分類說明），僅保留「縣市-行政區」。"""
    parts = [p for p in text.split(" ") if p and "(" not in p]
    return "".join(parts[:2]) if len(parts) >= 2 else text
