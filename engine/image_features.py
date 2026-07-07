"""Deterministic image features — describe an image WITHOUT any LLM/model.

WHY: when an image is attached to a NON-vision model, the degrade path
(`handlers/chat.py` → `_describe_image_with_vision`) used to always call a
vision LLM to *describe* it. This module produces model-free FACTS about the
image instead — dimensions, EXIF (camera/date/GPS), dominant colours,
brightness, a photo-vs-graphic heuristic, a face COUNT (Haar cascade), and
decoded QR/barcodes. Combined with OCR text upstream, a text-only model often
gets enough ("a receipt, total 119,00 EUR" + "JPEG 4032x3024, taken 2026-07-03,
2 faces") without any inference. The vision LLM stays only as a FALLBACK when
these deterministic signals come up empty (a textless photo of a scene).

These are FACTS, not interpretation — no "a person at the beach". OpenCV +
Pillow only (both already installed). QR/barcodes use OpenCV's own detectors
(cv2.QRCodeDetector / cv2.barcode) — NOT pyzbar, which segfaults on this
Python/macOS and would take the whole server process down (a segfault can't be
caught by try/except).
"""

from __future__ import annotations

import base64
import io
import os


def _fmt_gps(exif: dict) -> str | None:
    """Decode EXIF GPS to a 'lat, lon' string, or None."""
    try:
        from PIL.ExifTags import GPSTAGS
        gps_ifd = exif.get("GPSInfo")
        if not gps_ifd:
            return None
        g = {GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}

        def _to_deg(dms, ref):
            d, m, s = [float(x) for x in dms]
            val = d + m / 60.0 + s / 3600.0
            if ref in ("S", "W"):
                val = -val
            return round(val, 5)

        if "GPSLatitude" in g and "GPSLongitude" in g:
            lat = _to_deg(g["GPSLatitude"], g.get("GPSLatitudeRef", "N"))
            lon = _to_deg(g["GPSLongitude"], g.get("GPSLongitudeRef", "E"))
            return f"{lat}, {lon}"
    except Exception:
        return None
    return None


def _exif_facts(pil_img) -> list[str]:
    """Camera make/model, capture date, GPS — whatever EXIF carries."""
    facts = []
    try:
        from PIL.ExifTags import TAGS
        raw = pil_img.getexif()
        if not raw:
            return facts
        exif = {}
        for k, v in raw.items():
            exif[TAGS.get(k, k)] = v
        # GPSInfo is a sub-IFD
        try:
            gps = raw.get_ifd(0x8825)
            if gps:
                exif["GPSInfo"] = gps
        except Exception:
            pass

        make = str(exif.get("Make", "")).strip("\x00 ").strip()
        model = str(exif.get("Model", "")).strip("\x00 ").strip()
        cam = " ".join(x for x in (make, model) if x)
        if cam:
            facts.append(f"Kamera {cam}")
        dt = exif.get("DateTimeOriginal") or exif.get("DateTime")
        if dt:
            facts.append(f"aufgenommen {str(dt).strip()}")
        gps = _fmt_gps(exif)
        if gps:
            facts.append(f"GPS {gps}")
    except Exception:
        pass
    return facts


# Rough English colour names for the dominant BGR clusters.
_COLOR_NAMES = [
    ((0, 0, 0), "Schwarz"), ((255, 255, 255), "Weiß"), ((128, 128, 128), "Grau"),
    ((255, 0, 0), "Rot"), ((0, 128, 0), "Grün"), ((0, 0, 255), "Blau"),
    ((255, 255, 0), "Gelb"), ((255, 165, 0), "Orange"), ((128, 0, 128), "Violett"),
    ((165, 110, 40), "Braun"), ((0, 255, 255), "Cyan"), ((255, 192, 203), "Rosa"),
]


def _color_name(rgb) -> str:
    r, g, b = [int(x) for x in rgb]
    best, name = None, "?"
    for (cr, cg, cb), n in _COLOR_NAMES:
        d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
        if best is None or d < best:
            best, name = d, n
    return name


def _dominant_colors(cv_img, k: int = 3) -> list[str]:
    """k-means dominant colours → colour names, most-frequent first."""
    import cv2
    import numpy as np
    small = cv2.resize(cv_img, (80, 80), interpolation=cv2.INTER_AREA)
    data = small.reshape(-1, 3).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
    # deterministic init (fixed attempts, PP centers) — no RNG dependence
    _, labels, centers = cv2.kmeans(data, k, None, crit, 3,
                                    cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.flatten(), minlength=k)
    order = np.argsort(-counts)
    names = []
    for i in order:
        b, g, r = centers[i]
        n = _color_name((r, g, b))
        if n not in names:
            names.append(n)
    return names


def _count_faces(cv_img) -> int:
    """Frontal-face count via the OpenCV Haar cascade (deterministic)."""
    import cv2
    try:
        casc_path = os.path.join(cv2.data.haarcascades,
                                 "haarcascade_frontalface_default.xml")
        casc = cv2.CascadeClassifier(casc_path)
        if casc.empty():
            return 0
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
        faces = casc.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                      minSize=(30, 30))
        return len(faces)
    except Exception:
        return 0


def _decode_codes(cv_img) -> list[str]:
    """Decode QR codes and barcodes with OpenCV (already installed — no pyzbar,
    which segfaults on this Python/macOS). Deterministic; the code CONTENT is
    exact (like OCR). Best-effort: any failure yields []."""
    import cv2
    out = []
    # QR codes (multi)
    try:
        qr = cv2.QRCodeDetector()
        ok, decoded, _pts, _ = qr.detectAndDecodeMulti(cv_img)
        if ok:
            for d in decoded:
                d = (d or "").strip()
                if d:
                    out.append(f"QRCODE: {d}")
    except Exception:
        pass
    # Linear barcodes (EAN/UPC/Code128…) — opencv-contrib barcode module
    try:
        bd = cv2.barcode.BarcodeDetector()
        res = bd.detectAndDecode(cv_img)
        # API returns (retval, decoded_info, decoded_type, points) across
        # versions; be tolerant of the tuple shape.
        infos = res[1] if isinstance(res, tuple) and len(res) >= 2 else None
        types = res[2] if isinstance(res, tuple) and len(res) >= 3 else None
        if infos:
            for i, info in enumerate(infos):
                info = (info or "").strip()
                if info:
                    t = ""
                    try:
                        t = str(types[i]) if types is not None else ""
                    except Exception:
                        t = ""
                    out.append(f"{t or 'BARCODE'}: {info}")
    except Exception:
        pass
    return out


def describe_image_features(image_bytes: bytes, filename: str = "") -> dict:
    """Deterministic, model-free description of an image. Returns
    {facts: [str], faces: int, codes: [str], has_signal: bool} where
    has_signal is True when we learned something beyond bare dimensions
    (faces, codes, or a confident photo/graphic classification with colours) —
    the caller uses it to decide whether a vision-LLM fallback is still needed.
    """
    import cv2
    import numpy as np
    from PIL import Image

    facts: list[str] = []
    faces = 0
    codes: list[str] = []
    try:
        pil = Image.open(io.BytesIO(image_bytes))
        pil.load()
    except Exception as e:
        return {"facts": [f"(Bild nicht lesbar: {e})"], "faces": 0,
                "codes": [], "has_signal": False}

    w, h = pil.size
    fmt = (pil.format or "?").upper()
    facts.append(f"{fmt} {w}×{h} px")
    facts.extend(_exif_facts(pil))

    # → OpenCV BGR for pixel analysis
    rgb = pil.convert("RGB")
    cv_img = cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)

    # brightness
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    bright = round(float(gray.mean()) / 255.0, 2)
    facts.append(f"Helligkeit {bright} ({'hell' if bright > 0.6 else 'dunkel' if bright < 0.35 else 'mittel'})")

    # dominant colours
    try:
        colors = _dominant_colors(cv_img)
        if colors:
            facts.append("dominant " + "/".join(colors[:3]))
    except Exception:
        pass

    # photo vs graphic: edge density + colour-count heuristic. Graphics/
    # screenshots have flat regions (few unique colours, sharp edges);
    # photos have high colour variety.
    edges = cv2.Canny(cv_img, 100, 200)
    edge_density = float((edges > 0).mean())
    uniq = len(np.unique(cv_img.reshape(-1, 3), axis=0))
    total = w * h
    color_ratio = uniq / max(1, total)
    is_photo = color_ratio > 0.02 and not (edge_density > 0.08 and color_ratio < 0.01)
    kind = "Foto" if is_photo else "Grafik/Screenshot"
    facts.append(f"Typ={kind}")

    # faces (only meaningful on photos; cheap enough to always run)
    faces = _count_faces(cv_img)
    if faces:
        facts.append(f"{faces} Gesicht(er) erkannt")

    # QR / barcodes (OpenCV on the BGR image)
    codes = _decode_codes(cv_img)

    has_signal = bool(faces or codes or is_photo or len(facts) > 4)
    return {"facts": facts, "faces": faces, "codes": codes,
            "has_signal": has_signal}


def features_to_text(feat: dict, filename: str = "") -> str:
    """Render the feature dict as a compact human/model-readable line."""
    parts = list(feat.get("facts") or [])
    for c in feat.get("codes") or []:
        parts.append(f"Code {c}")
    body = ", ".join(parts) if parts else "keine Merkmale erkannt"
    prefix = f"{filename}: " if filename else ""
    return prefix + body
