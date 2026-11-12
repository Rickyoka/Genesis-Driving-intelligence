import io
from datetime import date, timedelta

from conftest import VEHICLE

PAGES = ["/", "/garage", "/courses", "/courses?mode=gt&weather=rain&night=1&tag=mountain",
         "/courses/jianjae", "/courses/jianjae?mode=touring&weather=snow", "/analyze",
         "/academy", "/passport", "/healthz", "/sample.csv"]


def test_all_pages_render(client):
    for url in PAGES:
        res = client.get(url)
        assert res.status_code == 200, url


def test_security_headers(client):
    res = client.get("/")
    assert "script-src 'self'" in res.headers["Content-Security-Policy"]
    assert res.headers["X-Frame-Options"] == "DENY"
    assert res.headers["X-Content-Type-Options"] == "nosniff"


def test_invalid_filters_fall_back(client):
    res = client.get("/courses?mode=<script>&weather=x&tag=zzz")
    assert res.status_code == 200
    assert b"<script>&" not in res.data


def test_unknown_course_404(client):
    assert client.get("/courses/nope").status_code == 404
    assert client.get("/reports/999").status_code == 404


def test_post_without_csrf_rejected(client):
    assert client.post("/garage", data=VEHICLE).status_code == 400


def test_vehicle_flow(client, token):
    res = client.post("/garage", data={**VEHICLE, "csrf_token": token}, follow_redirects=True)
    assert "테스트 카이맨" in res.get_data(as_text=True)
    page = client.get("/courses/jianjae").get_data(as_text=True)
    assert "테스트 카이맨" in page
    assert client.post("/garage/1/delete", data={"csrf_token": token}).status_code == 302
    assert client.post("/garage/1/delete", data={"csrf_token": token}).status_code == 404


def test_vehicle_validation(client, token):
    bad = {**VEHICLE, "power_ps": "99999", "csrf_token": token}
    res = client.post("/garage", data=bad, follow_redirects=True)
    assert "범위" in res.get_data(as_text=True)
    bad = {**VEHICLE, "drivetrain": "X", "csrf_token": token}
    res = client.post("/garage", data=bad, follow_redirects=True)
    assert "구동 방식" in res.get_data(as_text=True)


def test_xss_escaped(client, token):
    payload = {**VEHICLE, "nickname": "<img src=x onerror=alert(1)>", "csrf_token": token}
    client.post("/garage", data=payload)
    html = client.get("/garage").get_data(as_text=True)
    assert "<img src=x" not in html and "&lt;img" in html


def test_favorite_toggle(client, token):
    client.post("/courses/manhangjae/favorite", data={"csrf_token": token})
    assert "만항재" in client.get("/courses?fav=1").get_data(as_text=True)
    client.post("/courses/manhangjae/favorite", data={"csrf_token": token})
    assert "만항재" not in client.get("/courses?fav=1").get_data(as_text=True)


def test_sample_analysis_and_report(client, token):
    res = client.post("/analyze", data={"use_sample": "1", "csrf_token": token})
    assert res.status_code == 302 and "/reports/1" in res.headers["Location"]
    html = client.get("/reports/1").get_data(as_text=True)
    assert "종합 안정성" in html and "제동 안정성" in html
    assert "AI Driving Academy" in html
    assert client.get("/academy").status_code == 200
    assert client.get("/passport").status_code == 200


def test_upload_csv(client, token):
    rows = "\n".join(f"{i*0.1:.1f},{50 + (i % 40)},0.0,0.05" for i in range(1500))
    data = {"csrf_token": token,
            "logfile": (io.BytesIO(("timestamp_s,speed_kmh,accel_long_g,accel_lat_g\n" + rows).encode()), "drive.csv")}
    res = client.post("/analyze", data=data, content_type="multipart/form-data")
    assert res.status_code == 302 and "/reports/" in res.headers["Location"]


def test_upload_rejects_non_csv_and_bad_content(client, token):
    data = {"csrf_token": token, "logfile": (io.BytesIO(b"hello"), "evil.exe")}
    res = client.post("/analyze", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert "CSV 파일" in res.get_data(as_text=True)
    data = {"csrf_token": token, "logfile": (io.BytesIO(b"a,b\n1,2\n"), "x.csv")}
    res = client.post("/analyze", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert "필수 열" in res.get_data(as_text=True)


def test_upload_too_large(app, client, token):
    app.config["MAX_CONTENT_LENGTH"] = 1024
    data = {"csrf_token": token, "logfile": (io.BytesIO(b"0" * 5000), "big.csv")}
    res = client.post("/analyze", data=data, content_type="multipart/form-data")
    assert res.status_code == 413


def test_education_flow(client, token):
    ok = {"csrf_token": token, "program_id": "safe-braking", "school": "테스트 스쿨",
          "completed_on": date.today().isoformat()}
    html = client.post("/passport/education", data=ok, follow_redirects=True).get_data(as_text=True)
    assert "자기 신고 · 미검증" in html
    future = {**ok, "completed_on": (date.today() + timedelta(days=3)).isoformat()}
    html = client.post("/passport/education", data=future, follow_redirects=True).get_data(as_text=True)
    assert "오늘 이전" in html
    forged = {**ok, "verified": "1"}
    client.post("/passport/education", data=forged)
    html = client.get("/passport").get_data(as_text=True)
    assert "공식 수료 · 스쿨 확인" not in html  # users cannot self-verify
    bad = {**ok, "program_id": "nope"}
    html = client.post("/passport/education", data=bad, follow_redirects=True).get_data(as_text=True)
    assert "교육 프로그램 값" in html


def test_twin_line_cosmos_background(client):
    home = client.get("/").get_data(as_text=True)
    assert 'class="cosmos" data-cosmos' in home and "data-cosmos-anchor" in home
    assert "js/cosmos.js" in home
    courses = client.get("/courses").get_data(as_text=True)
    assert "data-cosmos" in courses and "data-cosmos-anchor" not in courses  # lines only, source above the page
    for url in ("/drive", "/hud"):
        page = client.get(url).get_data(as_text=True)
        assert "data-cosmos" not in page and "js/cosmos.js" not in page    # road renderer pages stay clean
    js = client.get("/static/js/cosmos.js")
    assert js.status_code == 200 and b"requestAnimationFrame" in js.data
