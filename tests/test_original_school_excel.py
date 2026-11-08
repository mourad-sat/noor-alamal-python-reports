"""Regression coverage for importing untouched school-export Excel files."""
from datetime import datetime
from io import BytesIO
import html
import re

from openpyxl import Workbook

from test_app import client, login
from test_platform_v2 import setup_org, post


def school_export():
    wb = Workbook()
    ws = wb.active
    ws.title = "3ANG-1"
    ws["C2"] = "لائحة التلاميذ"
    ws["A5"] = "الأكاديمية:"
    ws["C5"] = "الرباط - سلا - القنيطرة"
    ws["F6"] = "المؤسسة:"
    ws["G6"] = "دار الشباب لعيايدة"
    ws["G7"] = "2026/2027"
    ws["C8"] = "3ANG-1"
    ws.append([]) if ws.max_row < 9 else None
    for col, header in enumerate(("ر.ت", "الرمز", "النسب ", "الإسم", "النوع", "تاريخ الإزدياد", "مكان الازدياد"), start=1):
        ws.cell(10, col, header)
    for i in range(1, 18):
        ws.cell(10+i, 1, i)
        ws.cell(10+i, 2, f"TST-{i:03d}")
        ws.cell(10+i, 3, "العلوي")
        ws.cell(10+i, 4, f"مستفيد {i}")
        ws.cell(10+i, 5, "ذكر" if i % 2 else "أنثى")
        ws.cell(10+i, 6, datetime(2010, 1, 1 + (i % 27)))
        ws.cell(10+i, 7, "سلا")
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


def test_school_header_detection_and_only_existing_values(client):
    from platform_v2_imports import parse_upload, clean_row, normalize
    data = school_export()
    assert normalize("الإزدياد") == normalize("الازدياد")
    assert normalize("الإسم") == normalize("الاسم")
    parsed = parse_upload("ListEleve_20261108.xlsx", data)
    assert len(parsed) == 17
    assert parsed[0][0] == 11
    assert parsed[-1][0] == 27
    first = clean_row(parsed[0][1])
    assert first["full_name"] == "مستفيد 1 العلوي"
    assert first["source_code"] == "TST-001"
    assert first["born_on"] == "2010-01-02"
    assert first["birthplace"] == "سلا"
    assert first["guardian_phone"] == ""


def test_original_excel_17_rows_preview_and_confirm(client):
    admin = setup_org(client)
    result = client.post("/platform/import/preview", data={"_csrf": admin, "track_id": "1"},
                         files={"file": ("ListEleve_20261108.xlsx", school_export(),
                                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert result.status_code == 200, result.text[:400]
    assert "صفوف صالحة: 17" in result.text
    assert "مرفوضة: 0" in result.text
    assert "TST-001" in result.text
    match_payload = re.search('name="payload" value="([^"]+)"', result.text)
    match_signature = re.search('name="signature" value="([^"]+)"', result.text)
    assert match_payload and match_signature
    saved = post(client, "/platform/import/commit", admin,
                 payload=html.unescape(match_payload.group(1)),
                 signature=match_signature.group(1))
    assert saved.status_code == 200
    assert "تم إدخال 17 مستفيدًا" in saved.text
    info = client.get("/platform/learners/1")
    assert info.status_code == 200
    assert "TST-001" in info.text
    assert "مستفيد 1 العلوي" in info.text
    assert "يرجى استكماله لاحقًا" in info.text
    assert 'name="source_code"' in client.get("/platform/learners/1/edit").text
    # Prevent duplicate import of the same learner references.
    again = client.post("/platform/import/preview", data={"_csrf": admin, "track_id": "1"},
                        files={"file": ("ListEleve_20261108.xlsx", school_export(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert "صفوف صالحة: 0" in again.text
    assert "مرفوضة: 17" in again.text


def test_school_xlsx_without_learner_headers_rejected(client):
    from platform_v2_imports import parse_upload
    w = Workbook()
    w.active["A1"] = "لائحة غير مكتملة"
    f = BytesIO()
    w.save(f)
    from fastapi import HTTPException
    import pytest
    with pytest.raises(HTTPException) as err:
        parse_upload("bad.xlsx", f.getvalue())
    assert err.value.status_code == 422
