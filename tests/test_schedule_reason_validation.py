"""Regression: schedule creation must never show FastAPI missing-body JSON for reason."""
from html.parser import HTMLParser
from test_app import client, login
from test_platform_v2 import setup_org, post


class Forms(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.active = None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.active = {"action": attrs.get("action"), "fields": []}
            self.forms.append(self.active)
        if tag in ("input", "select", "textarea") and self.active is not None:
            self.active["fields"].append((tag, attrs))
    def handle_endtag(self, tag):
        if tag == "form":
            self.active = None


def form_for(html, path):
    parser = Forms()
    parser.feed(html)
    return next(form for form in parser.forms if form["action"] == path)


def slot_values(**overrides):
    values = dict(
        track_id=1, staff_id=3, subject="مهني",
        weekday=4, starts_at="09:00", ends_at="11:00",
    )
    values.update(overrides)
    return values


def test_reason_textarea_is_inside_correct_schedule_form_and_required(client):
    setup_org(client)
    html = client.get("/platform/schedule?center_id=1").text
    form = form_for(html, "/platform/schedule")
    reasons = [attrs for tag, attrs in form["fields"]
               if tag == "textarea" and attrs.get("name") == "reason"]
    assert len(reasons) == 1
    assert reasons[0]["required"] is None
    assert reasons[0]["minlength"] == "5"
    assert reasons[0]["maxlength"] == "1000"


def test_omitted_or_short_schedule_reason_is_friendly_arabic_html(client):
    csrf = setup_org(client)
    for overrides in ({}, {"reason": ""}, {"reason": "abc"}):
        response = post(client, "/platform/schedule", csrf,
                        **slot_values(**overrides))
        assert response.status_code == 422
        assert response.headers["content-type"].startswith("text/html")
        assert "سبب برمجة الحصة إلزامي" in response.text
        assert 'href="/platform/schedule"' in response.text
        assert '"type":"missing"' not in response.text


def test_valid_reason_adds_slot_and_returns_to_selected_center(client):
    csrf = setup_org(client)
    response = post(client, "/platform/schedule", csrf,
                    **slot_values(reason="استعمال الزمن الأسبوعي للمركز"))
    assert response.status_code == 303
    assert response.headers["location"] == "/platform/schedule?center_id=1"
    schedule = client.get(response.headers["location"])
    assert schedule.status_code == 200
    assert "09:00–11:00" in schedule.text
