import io
import os
import re
import zipfile
from typing import List, Tuple

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

ALLOWED_OUTPUT = {"jpg", "jpeg", "png", "webp"}
ALLOWED_QUALITY = {"standard", "high", "max"}

QUALITY_SETTINGS = {
    "standard": {"jpeg": 82, "webp": 80},
    "high": {"jpeg": 92, "webp": 90},
    "max": {"jpeg": 96, "webp": 95},
}


def safe_stem(filename: str) -> str:
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return stem or "image"


def convert_image_bytes(file_storage, out_fmt: str, quality: str = "standard") -> Tuple[bytes, str, str]:
    """
    Convert one uploaded image to the requested output format.

    quality:
      - standard: ad-supported free conversion
      - high/max: paid/pro conversion levels
    """
    out_fmt = (out_fmt or "").lower().strip()
    quality = (quality or "standard").lower().strip()

    if out_fmt not in ALLOWED_OUTPUT:
        raise ValueError("Invalid output format.")
    if quality not in ALLOWED_QUALITY:
        raise ValueError("Invalid quality level.")

    img = Image.open(file_storage.stream)
    img = ImageOps.exif_transpose(img)  # fix iPhone orientation from EXIF

    if out_fmt in {"jpg", "jpeg"}:
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")

    out = io.BytesIO()
    settings = QUALITY_SETTINGS[quality]

    if out_fmt in {"jpg", "jpeg"}:
        img.save(out, format="JPEG", quality=settings["jpeg"], optimize=True, progressive=True)
        return out.getvalue(), "jpg", "image/jpeg"

    if out_fmt == "png":
        # PNG is lossless; quality selector mainly controls access to larger batches/pro workflow.
        img.save(out, format="PNG", optimize=True)
        return out.getvalue(), "png", "image/png"

    img.save(out, format="WEBP", quality=settings["webp"], method=6)
    return out.getvalue(), "webp", "image/webp"


def build_zip(files: List[Tuple[str, bytes]]) -> io.BytesIO:
    """Build a zip from a list of (filename, bytes)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        used = set()
        for i, (name, data) in enumerate(files, start=1):
            if name in used:
                root, ext = os.path.splitext(name)
                name = f"{root}_{i}{ext}"
            used.add(name)
            zf.writestr(name, data)
    buf.seek(0)
    return buf
