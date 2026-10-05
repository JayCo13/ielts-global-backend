"""Shared upload validation.

Client-supplied Content-Type and filename extensions are spoofable, so uploads
must be validated by their real magic bytes and bounded in size. Use this for
every user-reachable image upload (affiliate QR, profile image, notification
images, …) to prevent a .svg/.html masquerading as an image (stored XSS when
served back from /static) and memory-exhaustion via unbounded reads.
"""
from typing import Optional

from fastapi import HTTPException

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB

# Magic-byte prefixes → canonical, safe (non-executable) extension.
_IMAGE_SIGNATURES = {
    b"\xff\xd8\xff": ".jpg",             # JPEG
    b"\x89PNG\r\n\x1a\n": ".png",        # PNG
    b"GIF87a": ".gif",
    b"GIF89a": ".gif",
    b"BM": ".bmp",
}


def sniff_image_ext(data: bytes) -> Optional[str]:
    """Return a safe extension if `data` really is a supported raster image, else None."""
    for sig, ext in _IMAGE_SIGNATURES.items():
        if data.startswith(sig):
            return ext
    # WEBP: "RIFF"<4 bytes size>"WEBP"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def validate_image_bytes(data: bytes, max_bytes: int = MAX_IMAGE_BYTES) -> str:
    """Validate raw upload bytes; return the canonical safe extension or raise HTTP 400/413.

    NOTE: SVG is intentionally rejected — it is XML and can carry scripts.
    """
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Image exceeds {max_bytes // (1024 * 1024)}MB")
    ext = sniff_image_ext(data)
    if not ext:
        raise HTTPException(status_code=400, detail="Not a valid image (JPG/PNG/GIF/WEBP only)")
    return ext
