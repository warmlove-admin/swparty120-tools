from __future__ import annotations

import glob
import re
import sys
import zipfile
import xml.etree.ElementTree as ET


NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.findall(".//a:t", NS)) for si in root.findall("a:si", NS)]


def column_index(ref: str) -> int:
    letters = re.match(r"([A-Z]+)", ref).group(1)
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - 64
    return index - 1


def cell_value(cell, shared_strings: list[str]) -> str:
    value = cell.find("a:v", NS)
    text = value.text if value is not None else ""
    if cell.attrib.get("t") == "s" and text:
        return shared_strings[int(text)]
    if cell.attrib.get("t") == "inlineStr":
        return "".join(t.text or "" for t in cell.findall(".//a:t", NS))
    return text


def first_sheet(zf: zipfile.ZipFile) -> tuple[str, str]:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    relmap = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
    sheet = workbook.findall("a:sheets/a:sheet", NS)[0]
    rel_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
    target = relmap[rel_id]
    path = "xl/" + target if not target.startswith("xl/") else target
    return sheet.attrib["name"], path


def preview(path: str, row_limit: int = 24) -> None:
    with zipfile.ZipFile(path) as zf:
        shared_strings = read_shared_strings(zf)
        sheet_name, sheet_path = first_sheet(zf)
        root = ET.fromstring(zf.read(sheet_path))
        print(f"=== {path} :: {sheet_name}")
        for row in root.findall("a:sheetData/a:row", NS)[:row_limit]:
            values: list[str] = []
            for cell in row.findall("a:c", NS):
                index = column_index(cell.attrib["r"])
                while len(values) <= index:
                    values.append("")
                values[index] = cell_value(cell, shared_strings)
            print(row.attrib.get("r", ""), " | ".join(values[:24]))


def main() -> int:
    pattern = sys.argv[1]
    for path in sorted(glob.glob(pattern))[:5]:
        preview(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
