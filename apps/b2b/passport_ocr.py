"""O'zbekiston biometrik shaxs guvohnomasi (ID karta) rasmlaridan ma'lumot olish.

Old tomon rasmidan (bosma matn OCR orqali) ism-familiya, otasining ismi,
tug'ilgan sana va karta seriya/raqami olinadi — bu maydonlar aynan shu tomonda
bosilgan bo'ladi. Orqa tomondagi MRZ (machine-readable zone) esa faqat PNFL
manbai sifatida ishlatiladi, chunki PNFL boshqa hech qayerda ochiq matn
sifatida bosilmagan, faqat MRZ'ning 1-qatoridagi optional maydonida kodlangan.

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

_FRONT_STRONG_MARKERS = ("GUVOHNOMASI",)
_FRONT_WEAK_MARKERS = ("IDENTITY CARD",)
_FRONT_COUNTRY_MARKERS = ("UZBEKISTON", "OZBEKISTON")

# Rasm kamera bilan olinganda karta ko'pincha tekis emas, hatto 90/180 gradusga
# burilgan holda tushishi mumkin — EXIF faqat qurilma sensorining aylanishini
# to'g'rilaydi, kartaning qo'lda qanday tutilganini emas. Shu sababli barcha
# asosiy burilishlarni sinab ko'ramiz.
_ROTATIONS = (0, 90, 180, 270)
_PSM_MODES = (6, 11, 4)
# None = faqat autocontrast. Kartadagi guilloche/gologramma fon naqshlari OCR
# matnini shovqinlashtiradi — qattiq threshold (binarizatsiya) fonni bosib,
# qalin qora matnni ajratib beradi, lekin yorug'lik sharoitiga qarab optimal
# qiymat farq qilishi mumkin, shuning uchun bir nechtasi sinab ko'riladi.
_THRESHOLDS = (None, 100, 120, 140)

_FRONT_FIELD_LABELS = {
    "surname": ("SURNAME", "FAMILIYASI"),
    "given_names": ("GIVEN NAME",),
    "patronymic": ("PATR",),  # "PATRONYMIC" OCR'da ko'pincha "PATRANYMIC" bo'lib o'qiladi
    "date_of_birth": ("DATE OF BIRTH", "TUGILGAN SANASI"),
    "card_number": ("CARD NUMBER", "KARTA RAQAMI"),
}
_ALL_FRONT_LABEL_KEYWORDS = tuple(kw for labels in _FRONT_FIELD_LABELS.values() for kw in labels)


def _preprocess_for_ocr(image: Image.Image, *, threshold: int | None = None) -> Image.Image:
    """Kichik/kontrastsiz/fon-naqshli telefon kamera suratlarida OCR aniqligini oshiradi."""
    image = image.convert("L")
    if threshold is not None:
        image = image.point(lambda x: 255 if x > threshold else 0)
    else:
        image = ImageOps.autocontrast(image)
    if image.width < 1800:
        scale = 1800 / image.width
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
    return image


def _load_raw_image(image_file) -> Image.Image:
    image_file.seek(0)
    image = ImageOps.exif_transpose(Image.open(image_file))
    image_file.seek(0)
    return image


def _ocr_image(image: Image.Image, *, whitelist: str | None = None, psm: int = 6) -> str:
    config = f"--psm {psm}"
    if whitelist:
        config += f" -c tessedit_char_whitelist={whitelist}"
    return pytesseract.image_to_string(image, config=config)


def _char_value(c: str) -> int:
    if c == "<":
        return 0
    if c.isdigit():
        return int(c)
    return ord(c) - ord("A") + 10


def _check_digit(field: str) -> int:
    return sum(_char_value(c) * _MRZ_WEIGHTS[i % 3] for i, c in enumerate(field)) % 10


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
    return pinfl


def extract_back_pinfl(back_file) -> str:
    """Orqa tomondagi MRZ'dan faqat PNFL'ni oladi (bu maydon boshqa joyda ochiq bosilmagan).

    Old tomondagi kabi, kamera burilishi va fon naqshlariga chidamli bo'lish
    uchun bir nechta burilish/threshold/segmentatsiya kombinatsiyasi sinaladi.
    """
    raw_image = _load_raw_image(back_file)

    last_error: PassportOCRError | None = None
    for degrees in _rotations_to_try(raw_image):
        rotated = raw_image.rotate(-degrees, expand=True) if degrees else raw_image
        for threshold in _THRESHOLDS:
            image = _preprocess_for_ocr(rotated, threshold=threshold)
            for psm in _PSM_MODES:
                text = _ocr_image(image, whitelist=_MRZ_CHARSET, psm=psm)
                try:
                    return _parse_mrz_pinfl(text)
                except PassportOCRError as exc:
                    last_error = exc

    raise last_error


def extract_passport_data(front_file, back_file) -> dict:
    """Old tomondan ism-familiya/tug'ilgan sana/karta raqamini, orqa tomondan PNFL'ni oladi."""
    passport_data = extract_front_data(front_file)
    passport_data["passport_pinfl"] = extract_back_pinfl(back_file)
    return passport_data
