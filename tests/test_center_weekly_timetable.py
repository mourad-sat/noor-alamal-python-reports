"""Each center has an independent, printable, role-scoped weekly timetable."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_app import client, login
from test_platform_v2 import post, setup_org, signin, signout


def make_slot(client, csrf, track, subject="مهني", staff=3, weekday=0,
              starts="09:00", ends="11:00", specific_date=None):
    kwargs = dict(track_id=track, staff_id=staff, subject=subject, weekday=weekday,
                  starts_at=starts, ends_at=ends,
                  reason="تثبيت استعمال الزمن الأسبوعي للمركز")
    if specific_date:
        kwargs["specific_date"] = specific_date
    return post(client, "/platform/schedule", csrf, **kwargs)


def test_admin_center_tabs_and_independent_weekly_grids(client):
    csrf = setup_org(client)
    assert make_slot(client, csrf, 1, starts="08:00", ends="10:00").status_code == 303
    assert make_slot(client, csrf, 2, starts="10:00", ends="12:00").status_code == 303

    one = client.get("/platform/schedule?center_id=1")
    two = client.get("/platform/schedule?center_id=2")
    assert one.status_code == two.status_code == 200
    assert 'href="/platform/schedule?center_id=1"' in one.text
    assert 'href="/platform/schedule?center_id=2"' in one.text
    assert 'href="/platform/schedule?center_id=1" aria-current="page"' in one.text
    assert 'href="/platform/schedule?center_id=2" aria-current="page"' in two.text

    first_grid = one.text.split('class="pv2-weekly-grid">', 1)[1].split('</section>', 1)[0]
    second_grid = two.text.split('class="pv2-weekly-grid">', 1)[1].split('</section>', 1)[0]
    assert first_grid.count('class="pv2-weekly-day"') == 7
    assert second_grid.count('class="pv2-weekly-day"') == 7
    assert "08:00–10:00" in first_grid and "10:00–12:00" not in first_grid
    assert "10:00–12:00" in second_grid and "08:00–10:00" not in second_grid
    assert 'name="center_id" value="1"' in one.text
    assert 'name="center_id" value="2"' in two.text


def test_center_specific_form_only_contains_center_tracks(client):
    csrf = setup_org(client)
    for center_id, expected, hidden in ((1, "1", "2"), (2, "2", "1")):
        response = client.get(f"/platform/schedule?center_id={center_id}")
        assert response.status_code == 200
        form = response.text.split(
            '<h2>إضافة حصة إلى استعمال الزمن الأسبوعي', 1
        )[1].split('</section>', 1)[0]
        track_select = form.split('<select name="track_id">', 1)[1].split('</select>', 1)[0]
        assert f'<option value="{expected}"' in track_select
        assert f'<option value="{hidden}"' not in track_select


def test_coordinator_cannot_access_other_center_and_instructor_cannot_manage(client):
    admin = setup_org(client)
    assert make_slot(client, admin, 1).status_code == 303
    signout(client, admin)
    coord = signin(client, "coord2", "strong-test-password-12345")
    assert client.get("/platform/schedule?center_id=1").status_code == 403
    own = client.get("/platform/schedule?center_id=2")
    assert own.status_code == 200
    assert "إضافة حصة إلى استعمال الزمن الأسبوعي" in own.text
    assert 'href="/platform/schedule?center_id=1"' not in own.text
    signout(client, coord)
    signin(client, "trainer", "strong-test-password-12345")
    personal = client.get("/platform/schedule?center_id=1")
    assert personal.status_code == 200
    assert "إضافة حصة إلى استعمال الزمن الأسبوعي" not in personal.text
    assert "تعديل الحصة" not in personal.text


def test_one_off_sessions_are_in_details_not_recurring_grid(client):
    admin = setup_org(client)
    one_off = make_slot(
        client, admin, 1, weekday=4, starts="14:00", ends="16:00",
        specific_date="2026-11-06",
    )
    assert one_off.status_code == 303
    html = client.get("/platform/schedule?center_id=1").text
    grid = html.split('class="pv2-weekly-grid">', 1)[1].split('</section>', 1)[0]
    details = html.split('class="pv2-weekly-details no-print"', 1)[1]
    assert "14:00–16:00" not in grid
    assert "14:00–16:00" in details
    assert "استثنائية" in details


def test_print_action_keeps_actual_center_specific_data(client):
    setup_org(client)
    html = client.get("/platform/schedule?center_id=1").text
    js = (__import__("pathlib").Path(__file__).resolve().parents[1] /
          "static" / "platform_ui.js").read_text(encoding="utf-8")
    css = (__import__("pathlib").Path(__file__).resolve().parents[1] /
           "static" / "platform_ui.css").read_text(encoding="utf-8")
    assert 'data-print-timetable' in html
    assert 'window.print()' in js
    assert '@page timetable{size:A4 landscape' in css
    assert '.pv2-neon-shell .pv2-weekly-grid{grid-template-columns:repeat(7' in css
