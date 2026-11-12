"""Vehicle photo handling: validate, sanitise and store user-uploaded images.

Security
--------
* Content is identified by decoding with Pillow (not by file name or MIME header).
* Only JPEG / PNG / WebP are accepted; decompression bombs are rejected.
* Every image is re-encoded to WebP: this strips EXIF (GPS location, device data) and
  neutralises polyglot files that hide scripts inside image containers.
* Stored names are random (uuid4) and validated by regex before any filesystem access,
  so a request can never address files outside the upload folder.
"""
from __future__ import annotations

import io
import os
import re
import uuid
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
MAX_PIXELS = 40_000_000           # ~ 8000 x 5000
MAX_EDGE = 1800                   # stored size (longest edge, px)
MIN_EDGE = 320
NAME_RE = re.compile(r"^[0-9a-f]{32}\.webp$")


class MediaError(ValueError):
    """User-safe validation error."""


def process_image(raw: bytes) -> bytes:
    if not raw:
        raise MediaError("이미지 파일을 선택하세요.")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as probe:
                fmt = probe.format
                probe.verify()                       # structural check
            if fmt not in ALLOWED_FORMATS:
                raise MediaError("JPG, PNG, WebP 이미지만 등록할 수 있습니다.")
            with Image.open(io.BytesIO(raw)) as im:  # verify() invalidates the object: reopen
                im.load()
                im = ImageOps.exif_transpose(im)     # respect camera orientation, then drop EXIF
                if min(im.size) < MIN_EDGE:
                    raise MediaError(f"이미지가 너무 작습니다 (짧은 변 {MIN_EDGE}px 이상).")
                has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
                im = im.convert("RGBA" if has_alpha else "RGB")
                im.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
                out = io.BytesIO()
                im.save(out, "WEBP", quality=86, method=4)   # no exif/icc passed -> metadata stripped
                return out.getvalue()
    except MediaError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise MediaError("이미지 해상도가 너무 큽니다.") from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise MediaError("이미지 파일을 읽을 수 없습니다. 손상되었거나 지원하지 않는 형식입니다.") from None


def save_image(folder: Path, data: bytes) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.webp"
    tmp = folder / (name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, folder / name)                   # atomic
    return name


def delete_image(folder: Path, name: str | None) -> None:
    if name and NAME_RE.match(name):
        (folder / name).unlink(missing_ok=True)


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name))
