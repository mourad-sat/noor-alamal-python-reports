"""All weekly timetable print modes remain role-scoped and model-driven."""
from test_app import client, login
from test_platform_v2 import setup_org, post, signin, signout


def add_slot(client, token, center, *, staff_id=3, day=0, time="08:30",
             end="10:30", subject="مهني", specific_date=None):
    fields = {
        "track_id": center, "staff_id": staff_id, "weekday": day,
        "starts_at": time, "ends_at": end, "subject": subject,
        "reason": "الجدول الأسبوعي الرسمي المعتمد",
    }
    if specific_date is not None:
        fields["specific_date"] = specific_date
    r = post(client, "/platform/schedule", token, **fields)
    assert r.status_code == 303, r.text[:400]


def prep_two_centers(client):
    admin = setup_org(client)
    add_slot(client, admin, 1, day=0, time="08:30", end="10:30")
    add_slot(client, admin, 2, day=1, time="13:30", end="15:30")
    return admin


def printed(html):
    return html.split('<main class="print-paper">', 1)[1]


def test_center_print_contains_only_its_center_and_matches_template(client):
    prep_two_centers(client)
    a = client.get("/platform/schedule/print?mode=center&center_id=1")
    b = client.get("/platform/schedule/print?mode=center&center_id=2")
    assert a.status_code == b.status_code == 200
    assert "text/html" in a.headers["content-type"]
    assert "no-store" in a.headers["cache-control"]
    assert 'href="/static/timetable_print.css"' in a.text
    assert "استعمال الزمن" in a.text
    assert "(فترة التدريس الصريح)" in a.text
    for field in ("المؤسسة:", "المستوى:", "الفوج:", "التخصص:",
                  "عدد التلاميذ:", "السنة الدراسية:"):
        assert field in printed(a.text)
    assert "2026/27" in printed(a.text)
    assert "دار الشباب لعيايدة 2" in printed(a.text)
    assert "08:30–10:30" in printed(a.text)
    assert "13:30–15:30" not in printed(a.text)
    assert "13:30–15:30" in printed(b.text)
    assert "08:30–10:30" not in printed(b.text)
    assert "الفترة الأولى" in printed(a.text)
    assert "الفترة الثانية" in printed(b.text)
    assert printed(a.text).count('scope="col"') == 7  # Corner + Mon–Sat


def test_print_all_centers_and_single_staff_across_both_centers(client):
    prep_two_centers(client)
    combined = client.get("/platform/schedule/print?mode=all")
    staff = client.get("/platform/schedule/print?mode=staff&staff_id=3")
    assert combined.status_code == staff.status_code == 200
    for body in (printed(combined.text), printed(staff.text)):
        assert "08:30–10:30" in body and "13:30–15:30" in body
        assert "دار الشباب لعيايدة 2" in body
        assert "مركز حمزة عبد المطلب" in body
    assert "استعمال الزمن الإجمالي لجميع مجموعات المركزين" in combined.text
    assert "المكوّن المشترك" in staff.text
    assert "استعمال الزمن الشخصي" in staff.text
    just_one = client.get("/platform/schedule/print?mode=staff&staff_id=3&center_id=1")
    assert just_one.status_code == 200
    assert "08:30–10:30" in printed(just_one.text)
    assert "13:30–15:30" not in printed(just_one.text)


def test_track_print_single_group_and_empty_print_keeps_blank_cells(client):
    prep_two_centers(client)
    group = client.get("/platform/schedule/print?mode=track&track_id=2")
    assert group.status_code == 200
    assert "13:30–15:30" in printed(group.text)
    assert "08:30–10:30" not in printed(group.text)
    assert "التسويق الإلكتروني" in group.text
    empty = client.get("/platform/schedule/print?mode=staff&staff_id=4")
    assert empty.status_code == 200
    assert "08:30–10:30" not in printed(empty.text)
    assert "13:30–15:30" not in printed(empty.text)
    assert 'class="print-blank"' in empty.text


def test_coordinator_sees_only_authorized_center_and_cannot_print_global(client):
    token = prep_two_centers(client)
    signout(client, token)
    signin(client, "coord1", "strong-test-password-12345")
    assert client.get("/platform/schedule/print?mode=all").status_code == 403
    assert client.get("/platform/schedule/print?mode=center&center_id=2").status_code == 403
    assert client.get("/platform/schedule/print?mode=track&track_id=2").status_code == 403
    own = client.get("/platform/schedule/print?mode=center&center_id=1")
    assert own.status_code == 200
    assert "08:30–10:30" in printed(own.text)
    assert "13:30–15:30" not in own.text
    staff = client.get("/platform/schedule/print?mode=staff&staff_id=3")
    assert staff.status_code == 200
    assert "13:30–15:30" not in printed(staff.text)


def test_instructor_only_prints_self_with_permitted_center_scoping(client):
    token = prep_two_centers(client)
    signout(client, token)
    signin(client, "trainer", "strong-test-password-12345")
    assert client.get("/platform/schedule/print?mode=all").status_code == 403
    assert client.get("/platform/schedule/print?mode=center&center_id=1").status_code == 403
    assert client.get("/platform/schedule/print?mode=staff&staff_id=4").status_code == 403
    personal = client.get("/platform/schedule/print")
    assert 'name="mode"' in personal.text
    assert personal.status_code == 200
    assert "08:30–10:30" in printed(personal.text)
    assert "13:30–15:30" in printed(personal.text)
    assert 'value="all"' not in personal.text


def test_bad_scope_and_year_rejected(client):
    setup_org(client)
    assert client.get("/platform/schedule/print?mode=invalid").status_code == 422
    assert client.get("/platform/schedule/print?mode=center&center_id=999").status_code == 403
    assert client.get("/platform/schedule/print?mode=track&track_id=999").status_code == 403
    assert client.get("/platform/schedule/print?mode=center&year_id=999").status_code == 404


def test_template_has_portrait_double_border_and_print_controls(client):
    setup_org(client)
    html = client.get("/platform/schedule/print").text
    assert "data-print-timetable" in html
    assert 'name="mode"' in html and 'name="year_id"' in html
    assert 'name="track_id"' in html and 'name="staff_id"' in html
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "static" / "timetable_print.css").read_text()
    assert "size:A4 portrait" in css
    assert "border:6px double #111" in css
    assert ".print-period-1" in css and ".print-period-2" in css
