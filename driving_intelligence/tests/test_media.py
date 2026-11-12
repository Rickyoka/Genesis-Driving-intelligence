"""Vehicle photo placement (v0.5): sanitising uploads and serving them safely."""
import io
import re
import sqlite3

import pytest
from PIL import Image

from app import create_app
from app.services import media
from conftest import VEHICLE


def png_bytes(w=800, h=450, color=(30, 40, 60), mode="RGB"):
    buf = io.BytesIO()
    Image.new(mode, (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def jpeg_with_gps():
    im = Image.new("RGB", (900, 500), (90, 90, 90))
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (37.0, 33.0, 0.0), 3: "E", 4: (126.0, 58.0, 0.0)}  # GPS IFD
    exif[0x010F] = "SecretPhone"                                                    # Make
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


# ---------------------------------------------------------------- service
def test_reencodes_to_webp_and_strips_exif():
    raw = jpeg_with_gps()
    assert b"SecretPhone" in raw
    out = media.process_image(raw)
    with Image.open(io.BytesIO(out)) as im:
        assert im.format == "WEBP"
        assert not im.getexif()
    assert b"SecretPhone" not in out


def test_resizes_large_and_keeps_alpha():
    out = media.process_image(png_bytes(3600, 2000, (10, 10, 10, 0), "RGBA"))
    with Image.open(io.BytesIO(out)) as im:
        assert max(im.size) == media.MAX_EDGE and im.mode == "RGBA"


@pytest.mark.parametrize("raw", [b"", b"not an image", b"GIF89a" + b"\x00" * 100,
                                  b"<script>alert(1)</script>", png_bytes()[:200]])
def test_rejects_non_images_and_truncated(raw):
    with pytest.raises(media.MediaError):
        media.process_image(raw)


def test_rejects_unsupported_format_tiny_and_bomb():
    gif = io.BytesIO(); Image.new("RGB", (800, 600)).save(gif, "GIF")
    with pytest.raises(media.MediaError):
        media.process_image(gif.getvalue())
    with pytest.raises(media.MediaError):
        media.process_image(png_bytes(200, 120))
    bomb = io.BytesIO(); Image.new("1", (9000, 6000)).save(bomb, "PNG")  # 54 MP, tiny file
    with pytest.raises(media.MediaError):
        media.process_image(bomb.getvalue())


def test_name_validation_blocks_traversal(tmp_path):
    for bad in ("../secret_key", "a.webp", "x" * 32 + ".webp", "0" * 32 + ".png", "..%2F" + "0" * 32 + ".webp"):
        assert not media.valid_name(bad)
    media.delete_image(tmp_path, "../../etc/passwd")  # must be a no-op, never raise
    name = media.save_image(tmp_path, b"data")
    assert media.valid_name(name) and (tmp_path / name).exists()


# ---------------------------------------------------------------- routes
def _upload(client, token, vid=1, data=None, rights="1"):
    form = {"csrf_token": token, "photo": (io.BytesIO(data or png_bytes()), "car.png")}
    if rights:
        form["rights"] = rights
    return client.post(f"/garage/{vid}/photo", data=form, content_type="multipart/form-data", follow_redirects=True)


def test_photo_flow_and_placement(app, client, token):
    client.post("/garage", data={**VEHICLE, "csrf_token": token})
    html = client.get("/").get_data(as_text=True)
    assert "img/concept-gt.svg" in html and "실존 차량 아님" in html     # default concept rendering
    res = _upload(client, token)
    assert "사진을 배치했습니다" in res.get_data(as_text=True)
    home = client.get("/").get_data(as_text=True)
    m = re.search(r'/media/vehicles/([0-9a-f]{32}\.webp)', home)
    assert m and "stage--photo" in home and 'img class="is-photo"' in home
    img = client.get(f"/media/vehicles/{m.group(1)}")
    assert img.status_code == 200 and img.mimetype == "image/webp"
    assert img.headers["X-Content-Type-Options"] == "nosniff"
    folder = app.instance_path + "/uploads/vehicles/"
    import os
    assert os.path.exists(folder + m.group(1))
    _upload(client, token)                                  # replace -> old file removed
    assert not os.path.exists(folder + m.group(1))
    client.post("/garage/1/delete", data={"csrf_token": token})
    assert os.listdir(folder) == []                         # vehicle delete cleans its photo


def test_photo_requires_rights_csrf_and_valid_image(client, token):
    client.post("/garage", data={**VEHICLE, "csrf_token": token})
    assert "사용 권한" in _upload(client, token, rights=None).get_data(as_text=True)
    assert "읽을 수 없습니다" in _upload(client, token, data=b"\x89PNG\r\n\x1a\nfake").get_data(as_text=True)
    r = client.post("/garage/1/photo", data={"photo": (io.BytesIO(png_bytes()), "a.png"), "rights": "1"},
                    content_type="multipart/form-data")
    assert r.status_code == 400
    assert _upload(client, token, vid=99).status_code == 404


def test_photo_delete_restores_concept(client, token):
    client.post("/garage", data={**VEHICLE, "csrf_token": token})
    _upload(client, token)
    client.post("/garage/1/photo/delete", data={"csrf_token": token})
    assert "img/concept-gt.svg" in client.get("/").get_data(as_text=True)


def test_media_route_rejects_bad_names(client):
    for bad in ("..%2Fsecret_key", "secret_key", "0" * 32 + ".png"):
        assert client.get(f"/media/vehicles/{bad}").status_code == 404


def test_migration_v3_to_v4_adds_photo(tmp_path):
    path = tmp_path / "v3.db"
    app = create_app({"TESTING": True, "SECRET_KEY": "x", "DATABASE": str(path), "ROUTE_PROVIDER": "offline"})
    c = sqlite3.connect(path)
    c.execute("ALTER TABLE vehicles DROP COLUMN photo"); c.execute("PRAGMA user_version = 3"); c.commit(); c.close()
    create_app({"TESTING": True, "SECRET_KEY": "x", "DATABASE": str(path), "ROUTE_PROVIDER": "offline"})
    c = sqlite3.connect(path)
    assert "photo" in {r[1] for r in c.execute("PRAGMA table_info(vehicles)")}
    assert c.execute("PRAGMA user_version").fetchone()[0] == 4
    del app
