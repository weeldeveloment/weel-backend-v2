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


def _guess_rotation(image: Image.Image) -> int | None:
    """Tesseract OSD orqali kartaning taxminiy burilishini tezda aniqlaydi.

    Bu faqat tezlik uchun optimallashtirish — agar taxmin noto'g'ri yoki
    aniqlanmasa, chaqiruvchi baribir barcha burilishlarni sinab ko'radi.
    """
    try:
        osd = pytesseract.image_to_osd(image, config="--psm 0")
    except pytesseract.TesseractError:
        return None
    match = re.search(r"Rotate:\s*(\d+)", osd)
    return int(match.group(1)) % 360 if match else None


def _rotations_to_try(raw_image: Image.Image) -> tuple[int, ...]:
    guess = _guess_rotation(_preprocess_for_ocr(raw_image))
    if guess is None or guess not in _ROTATIONS:
        return _ROTATIONS
    return (guess,) + tuple(d for d in _ROTATIONS if d != guess)


def _matches_front_template(text: str) -> bool:
    normalized = re.sub(r"[’'`]", "", text.upper())
    has_strong = any(marker in normalized for marker in _FRONT_STRONG_MARKERS)
    has_weak = any(marker in normalized for marker in _FRONT_WEAK_MARKERS)
    has_country = any(marker in normalized for marker in _FRONT_COUNTRY_MARKERS)
    return has_strong or (has_weak and has_country)


def _clean_name_value(line: str) -> str | None:
    """Qiymat qatoridagi eng uzun harflar ketma-ketligini oladi (OCR chetlab
    qo'ygan chiziqcha/nuqta kabi shovqinlarni tashlab yuborish uchun)."""
    words = re.findall(r"[A-Z]{2,}", line)
    if not words:
        return None
    return max(words, key=len)


def _extract_front_fields(text: str) -> dict:
    """OCR qilingan old tomon matnidan label qatori ostidagi qiymatlarni oladi.

    Karta shablonida har bir maydon "Label / Label" qatoridan so'ng qiymat
    qatori keladi (masalan "Familiyasi / Surname" dan keyin "SETDAROV").
    """
    lines = [re.sub(r"[’'`]", "", ln).strip().upper() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]

    def next_value_line(start_idx: int) -> str | None:
        for j in range(start_idx + 1, len(lines)):
            if any(kw in lines[j] for kw in _ALL_FRONT_LABEL_KEYWORDS):
                continue
            return lines[j]
        return None

    values: dict[str, str] = {}
    for i, line in enumerate(lines):
        for field, labels in _FRONT_FIELD_LABELS.items():
            if field in values:
                continue
            if any(label in line for label in labels):
                value_line = next_value_line(i)
                if value_line:
                    values[field] = value_line

    missing = [f for f in ("surname", "given_names", "date_of_birth", "card_number") if f not in values]
    if missing:
        raise PassportOCRError(
            "Old tomon rasmidan ism-familiya/tug'ilgan sana/karta raqamini aniqlab bo'lmadi. "
            "Rasmni tekis va yorug' joyda qayta yuklang."
        )

    surname = _clean_name_value(values["surname"])
    given_names = _clean_name_value(values["given_names"])
    if not surname or not given_names:
        raise PassportOCRError(
            "Old tomon rasmidan ism-familiyani aniqlab bo'lmadi. Rasmni tekis va yorug' joyda qayta yuklang."
        )
    patronymic = _clean_name_value(values["patronymic"]) if "patronymic" in values else None

    dob_match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", values["date_of_birth"])
    if not dob_match:
        raise PassportOCRError("Tug'ilgan sanani old tomondan aniqlab bo'lmadi.")
    day, month, year = (int(part) for part in dob_match.groups())
    date_of_birth = date(year, month, day)

    card_match = re.search(r"([A-Z]{2}\d{7})", values["card_number"].replace(" ", ""))
    if not card_match:
        raise PassportOCRError("Karta raqamini old tomondan aniqlab bo'lmadi.")
    card_number = card_match.group(1)

    full_name = " ".join(part for part in (surname, given_names, patronymic) if part)

    return {
        "full_name": full_name,
        "date_of_birth": date_of_birth,
        "passport_series": card_number,
    }


def extract_front_data(front_file) -> dict:
    """Old tomon rasmini shablon bo'yicha tekshiradi va undan xodim ma'lumotlarini oladi.

    Fotosurat sifati (burilish, yorug'lik, kartadagi gologramma fon naqshlari) OCR
    natijasiga kuchli ta'sir qilgani uchun bir nechta burilish/threshold/segmentatsiya
    kombinatsiyasi sinab ko'riladi va to'liq muvaffaqiyatli o'qilgan birinchi natija olinadi.
    """
    raw_image = _load_raw_image(front_file)

    template_matched = False
    last_field_error: PassportOCRError | None = None
    for degrees in _rotations_to_try(raw_image):
        rotated = raw_image.rotate(-degrees, expand=True) if degrees else raw_image
        for threshold in _THRESHOLDS:
            image = _preprocess_for_ocr(rotated, threshold=threshold)
            for psm in _PSM_MODES:
                text = _ocr_image(image, psm=psm)
                if not _matches_front_template(text):
                    continue
                template_matched = True
                try:
                    return _extract_front_fields(text)
                except PassportOCRError as exc:
                    last_field_error = exc

    if not template_matched:
        raise PassportOCRError("Old tomon rasmi namunaga (SHAXS GUVOHNOMASI old tomoni) mos kelmadi.")
    raise last_field_error


def _parse_mrz_pinfl(text: str) -> str:
    line1, _line2, _line3 = _find_mrz_lines(text)

    document_field = line1[5:14]
    if str(_check_digit(document_field)) != line1[14]:
        raise PassportOCRError("Orqa tomondagi MRZ kodini aniq o'qib bo'lmadi. Rasmni yaxshiroq sifatda qayta yuklang.")

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
