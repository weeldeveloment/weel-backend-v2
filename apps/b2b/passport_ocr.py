"""O'zbekiston biometrik shaxs guvohnomasi (ID karta) rasmlaridan ma'lumot olish.

Old tomon rasm faqat shablon tekshiruvi uchun ishlatiladi (bu haqiqatan ham
"SHAXS GUVOHNOMASI" ekanini OCR matnidan tasdiqlaydi) — ism-familiya, tug'ilgan
sana va passport seriya/raqami esa orqa tomondagi MRZ (machine-readable zone)
qatoridan olinadi, chunki u qat'iy pozitsion format va checksum'ga ega bo'lib,
erkin OCR matnidan ancha ishonchli.

MRZ format (ICAO 9303, TD1, 3 qator x 30 belgi):
  1-qator: hujjat kodi(2) + davlat(3) + hujjat raqami(9) + check(1) + optional(15)
  2-qator: tug'ilgan sana YYMMDD(6) + check(1) + jins(1) + amal muddati(8) + fuqarolik(3) + optional(11) + composite check(1)
  3-qator: FAMILIYA<<ISM<OTASINING_ISMI<<<<<<<<<<<<<<<<
O'zbekiston ID kartasida 1-qatordagi 15 xonali optional maydon 14 xonali PNFL'ni saqlaydi.
"""
from __future__ import annotations

import re
from datetime import date

import pytesseract
from PIL import Image, ImageOps


class PassportOCRError(Exception):
    """Passport rasmini shablon bo'yicha tekshirish yoki undan ma'lumot o'qishda xatolik."""


_MRZ_CHARSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
_MRZ_LINE_RE = re.compile(r"^[A-Z0-9<]{28,30}$")
_MRZ_WEIGHTS = (7, 3, 1)

_FRONT_TEMPLATE_MARKERS = ("GUVOHNOMASI", "IDENTITY CARD")
_FRONT_COUNTRY_MARKERS = ("UZBEKISTON", "OZBEKISTON")


def _ocr_text(image_file, *, whitelist: str | None = None) -> str:
    image_file.seek(0)
    image = ImageOps.exif_transpose(Image.open(image_file))
    config = "--psm 6"
    if whitelist:
        config += f" -c tessedit_char_whitelist={whitelist}"
    text = pytesseract.image_to_string(image, config=config)
    image_file.seek(0)
    return text


def _char_value(c: str) -> int:
    if c == "<":
        return 0
    if c.isdigit():
        return int(c)
    return ord(c) - ord("A") + 10


def _check_digit(field: str) -> int:
    return sum(_char_value(c) * _MRZ_WEIGHTS[i % 3] for i, c in enumerate(field)) % 10


def _mrz_birth_date(yymmdd: str) -> date:
    yy, mm, dd = int(yymmdd[0:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    current_yy = date.today().year % 100
    century = 1900 if yy > current_yy else 2000
    return date(century + yy, mm, dd)


def _find_mrz_lines(text: str) -> list[str]:
    candidates = []
    for raw_line in text.splitlines():
        line = raw_line.strip().upper().replace(" ", "")
        if _MRZ_LINE_RE.match(line):
            candidates.append(line.ljust(30, "<")[:30])
    for i in range(len(candidates) - 2):
        l1, l2, l3 = candidates[i:i + 3]
        if l1[2:5] == "UZB" and l2[0].isdigit() and "<<" in l3:
            return [l1, l2, l3]
    raise PassportOCRError(
        "Orqa tomondagi MRZ kodini o'qib bo'lmadi. Rasm aniqroq va yorug'roq sharoitda qayta yuklang."
    )


def verify_front_template(front_file) -> None:
    """Old tomon rasmi O'zbekiston SHAXS GUVOHNOMASI shabloniga mos kelishini tekshiradi."""
    text = _ocr_text(front_file).upper()
    normalized = re.sub(r"[’'`]", "", text)
    if not any(marker in normalized for marker in _FRONT_TEMPLATE_MARKERS):
        raise PassportOCRError("Old tomon rasmi namunaga (SHAXS GUVOHNOMASI old tomoni) mos kelmadi.")
    if not any(marker in normalized for marker in _FRONT_COUNTRY_MARKERS):
        raise PassportOCRError("Old tomon rasmi namunaga (SHAXS GUVOHNOMASI old tomoni) mos kelmadi.")


def extract_back_data(back_file) -> dict:
    """Orqa tomondagi MRZ'dan ism-familiya, tug'ilgan sana, passport seriya/raqami va PNFL'ni oladi."""
    text = _ocr_text(back_file, whitelist=_MRZ_CHARSET)
    line1, line2, line3 = _find_mrz_lines(text)

    document_field = line1[5:14]
    if str(_check_digit(document_field)) != line1[14]:
        raise PassportOCRError("Karta raqamini aniq o'qib bo'lmadi. Rasmni yaxshiroq sifatda qayta yuklang.")
    document_number = document_field.replace("<", "")
    if len(document_number) < 3:
        raise PassportOCRError("Karta raqamini aniq o'qib bo'lmadi. Rasmni yaxshiroq sifatda qayta yuklang.")

    birth_field = line2[0:6]
    if str(_check_digit(birth_field)) != line2[6]:
        raise PassportOCRError("Tug'ilgan sanani aniq o'qib bo'lmadi. Rasmni yaxshiroq sifatda qayta yuklang.")
    date_of_birth = _mrz_birth_date(birth_field)

    pinfl = line1[15:30].replace("<", "")
    if not re.fullmatch(r"\d{14}", pinfl):
        raise PassportOCRError("PNFL (14 xonali raqam) orqa tomondan aniqlanmadi.")

    names_field = line3.rstrip("<")
    if "<<" not in names_field:
        raise PassportOCRError("Ism-familiyani orqa tomondan aniqlab bo'lmadi.")
    surname_part, given_part = names_field.split("<<", 1)
    surname = surname_part.replace("<", " ").strip()
    given_names = given_part.replace("<", " ").strip()
    if not surname or not given_names:
        raise PassportOCRError("Ism-familiyani orqa tomondan aniqlab bo'lmadi.")

    return {
        "full_name": f"{surname} {given_names}".strip(),
        "date_of_birth": date_of_birth,
        "passport_series": document_number[:2],
        "passport_pinfl": pinfl,
    }


def extract_passport_data(front_file, back_file) -> dict:
    """Old tomonni shablon bo'yicha tekshiradi, so'ng orqa tomondagi MRZ'dan xodim ma'lumotlarini oladi."""
    verify_front_template(front_file)
    return extract_back_data(back_file)
