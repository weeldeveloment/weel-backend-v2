"""O'zbekiston biometrik shaxs guvohnomasi (ID karta) rasmlaridan ma'lumot olish.

Orqa tomondagi MRZ (machine-readable zone) asosiy ma'lumot manbai: undan
ism-familiya, tug'ilgan sana, karta raqami va PNFL — hammasi bitta OCR
o'qishidan olinadi. MRZ OCR-B degan maxsus, mashina o'qishi uchun optimallashtirilgan
shriftda bosiladi va raqamli maydonlar (karta raqami, tug'ilgan sana, PNFL)
check-digit bilan tasdiqlanadi, shu sababli u kartaning bezakli old tomon
yozuviga qaraganda ancha ishonchli va tezroq o'qiladi. Old tomon faqat MRZ'dan
ism-familiya olinmagan (masalan juda uzun ism MRZ maydoniga sig'may qolgan)
kamdan-kam holatlarda zaxira sifatida ishlatiladi.

MRZ format (ICAO 9303, TD1, 3 qator x 30 belgi):
  1-qator: hujjat kodi(2) + davlat(3) + hujjat raqami(9) + check(1) + optional(15)
  2-qator: tug'ilgan sana YYMMDD(6) + check(1) + jins(1) + amal muddati(8) + fuqarolik(3) + optional(11) + composite check(1)
  3-qator: FAMILIYA<<ISM<OTASINING_ISMI<<<<<<<<<<<<<<<<
O'zbekiston ID kartasida 1-qatordagi 15 xonali optional maydon 14 xonali PNFL'ni saqlaydi.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Callable, TypeVar

import pytesseract
from PIL import Image, ImageOps

_T = TypeVar("_T")
# Each Tesseract call shells out to a separate OS process, so running these
# concurrently gives real wall-clock parallelism (not limited by the
# Python GIL) — this is what actually cuts user-facing wait time, since the
# total OCR work per image is unavoidable (rotation/lighting/background
# noise all vary per photo, so multiple threshold/PSM attempts are still
# needed for accuracy).
_OCR_WORKERS = 6


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
# OCR endi so'rov-javob davri ichida emas, fon vazifasi (Celery) sifatida
# ishlaydi (qarang: apps.b2b.tasks.run_passport_ocr_job) — foydalanuvchi
# natijani kutib turmaydi, shuning uchun chaqiruvlar sonini kamaytirish
# orqali tezlashtirish endi shart emas. Buning o'rniga aniqlikni
# maksimallashtiramiz: real, xira/burchakli telefon suratlarida MRZ'ni
# o'qish uchun bir nechta segmentatsiya rejimi kerak bo'lishi mumkin.
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


# Tesseract'ning aniqligi bu kenglikdan yuqorida sezilarli oshmaydi, lekin
# ishlash vaqti taxminan rasm yuzasiga proportsional o'sadi. Zamonaviy
# telefon kameralari 3000-4000px kenglikdagi rasm berishi odatiy holat —
# shuni har bir (48 tagacha) Tesseract chaqiruvida to'liq o'lchamda qayta
# ishlash sekinlikning asosiy sababi edi. Shu sababli rasm hajmi BIR MARTA,
# yuklanganda, ushbu kenglikka normallashtiriladi (kichik rasmlar
# kattalashtiriladi, katta rasmlar kichraytiriladi).
_OCR_TARGET_WIDTH = 1800


def _preprocess_for_ocr(image: Image.Image, *, threshold: int | None = None) -> Image.Image:
    """Kichik/kontrastsiz/fon-naqshli telefon kamera suratlarida OCR aniqligini oshiradi."""
    image = image.convert("L")
    if threshold is not None:
        image = image.point(lambda x: 255 if x > threshold else 0)
    else:
        image = ImageOps.autocontrast(image)
    return image


def _load_raw_image(image_file) -> Image.Image:
    image_file.seek(0)
    image = ImageOps.exif_transpose(Image.open(image_file))
    image_file.seek(0)
    if image.width != _OCR_TARGET_WIDTH:
        scale = _OCR_TARGET_WIDTH / image.width
        image = image.resize(
            (_OCR_TARGET_WIDTH, max(1, round(image.height * scale))), Image.LANCZOS
        )
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


def _mrz_year_to_full(two_digit_year: int, *, today: date | None = None) -> int:
    """YY -> to'liq yil. Kelajakka chiqib ketsa (masalan bugun 2026'da YY=95),
    o'tgan asrga tegishli deb hisoblanadi — bu standart MRZ konventsiyasi."""
    today = today or date.today()
    century_base = (today.year // 100) * 100
    year = century_base + two_digit_year
    if year > today.year:
        year -= 100
    return year


def _parse_mrz_name(line3: str) -> tuple[str, str, str | None]:
    """3-qatordan FAMILIYA<<ISM<OTASINING_ISMI shablonini ajratadi.

    Otasining ismi ko'pincha ikki so'zdan iborat bo'ladi — masalan
    "ANVAR<OGLI" ("Anvar o'g'li") yoki "SODIQ<QIZI" ("Sodiq qizi") —
    shuning uchun ISM'dan keyingi qolgan HAMMA '<'-ajratilgan bo'laklar
    (faqat birinchisi emas) otasining ismiga qo'shib olinadi, aks holda
    "O'G'LI"/"QIZI" so'zi tashlanib, ism to'liqsiz chiqadi."""
    stripped = line3.rstrip("<")
    surname_raw, _, rest = stripped.partition("<<")
    given_parts = [p for p in rest.split("<") if p]
    surname = surname_raw.replace("<", " ").strip()
    given_names = given_parts[0] if given_parts else ""
    patronymic = " ".join(given_parts[1:]) if len(given_parts) > 1 else None
    return surname, given_names, patronymic


def _parse_mrz_full(text: str) -> dict:
    """Orqa tomon MRZ'sining barcha 3 qatoridan tekshirilgan (check-digit)
    ma'lumotlarni oladi: karta raqami, tug'ilgan sana va PNFL raqamli
    checksum bilan tasdiqlanadi; ism-familiya OCR-B shrifti standart bosma
    matnga qaraganda ancha ishonchli o'qiladi, shu sababli asosiy manba
    sifatida ishlatiladi."""
    line1, line2, line3 = _find_mrz_lines(text)

    document_field = line1[5:14]
    if str(_check_digit(document_field)) != line1[14]:
        raise PassportOCRError(
            "Orqa tomondagi MRZ kodini aniq o'qib bo'lmadi. Rasmni yaxshiroq sifatda qayta yuklang."
        )
    card_match = re.match(r"([A-Z]{2}\d{7})", document_field)
    if not card_match:
        raise PassportOCRError("Karta raqamini orqa tomon MRZ'dan aniqlab bo'lmadi.")
    card_number = card_match.group(1)

    pinfl = line1[15:30].replace("<", "")
    if not re.fullmatch(r"\d{14}", pinfl):
        raise PassportOCRError("PNFL (14 xonali raqam) orqa tomondan aniqlanmadi.")

    dob_field = line2[0:6]
    if not dob_field.isdigit() or str(_check_digit(dob_field)) != line2[6]:
        raise PassportOCRError(
            "Tug'ilgan sanani orqa tomon MRZ'dan aniq o'qib bo'lmadi. Rasmni yaxshiroq sifatda qayta yuklang."
        )
    yy, mm, dd = int(dob_field[0:2]), int(dob_field[2:4]), int(dob_field[4:6])
    try:
        date_of_birth = date(_mrz_year_to_full(yy), mm, dd)
    except ValueError as exc:
        raise PassportOCRError("Tug'ilgan sana orqa tomon MRZ'da noto'g'ri kodlangan.") from exc

    surname, given_names, patronymic = _parse_mrz_name(line3)
    if not surname or not given_names:
        full_name = ""
    else:
        full_name = " ".join(part for part in (surname, given_names, patronymic) if part)

    return {
        "full_name": full_name,
        "date_of_birth": date_of_birth,
        "passport_series": card_number,
        "passport_pinfl": pinfl,
    }


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


def _try_ocr_combos(
    rotated_image: Image.Image,
    *,
    whitelist: str | None,
    attempt: Callable[[str], _T | None],
) -> _T | None:
    """Berilgan burilishdagi rasm uchun barcha threshold/PSM kombinatsiyalarini
    PARALLEL sinaydi (ketma-ket emas) va ustuvorlik tartibidagi (threshold,
    keyin psm) birinchi muvaffaqiyatli natijani qaytaradi.

    ``attempt(text)`` uchta holatdan birini qaytarishi mumkin:
      - natija (masalan parslangan dict) — muvaffaqiyat;
      - ``None`` — bu OCR o'qishi umuman mos kelmadi (masalan shablon
        aniqlanmadi), xatolik emas;
      - ``PassportOCRError`` chiqarishi mumkin — mos keldi, lekin
        maydonlarni ajratib bo'lmadi.

    Agar hech qanday kombinatsiya natija bermasa: kamida bitta xatolik
    qayd etilgan bo'lsa o'sha qayta chiqariladi, aks holda ``None``
    qaytariladi (chaqiruvchi keyingi burilishga o'tishi mumkin)."""
    combos = [(threshold, psm) for threshold in _THRESHOLDS for psm in _PSM_MODES]
    preprocessed = {
        threshold: _preprocess_for_ocr(rotated_image, threshold=threshold)
        for threshold in _THRESHOLDS
    }

    def run(index: int) -> tuple[int, _T | PassportOCRError | None]:
        threshold, psm = combos[index]
        text = _ocr_image(preprocessed[threshold], whitelist=whitelist, psm=psm)
        try:
            return index, attempt(text)
        except PassportOCRError as exc:
            return index, exc

    results: list[_T | PassportOCRError | None] = [None] * len(combos)
    with ThreadPoolExecutor(max_workers=_OCR_WORKERS) as executor:
        for index, result in executor.map(run, range(len(combos))):
            results[index] = result

    last_error: PassportOCRError | None = None
    for result in results:
        if isinstance(result, PassportOCRError):
            last_error = result
        elif result is not None:
            return result
    if last_error is not None:
        raise last_error
    return None


def _matches_front_template(text: str) -> bool:
    normalized = re.sub(r"[’'`]", "", text.upper())
    has_strong = any(marker in normalized for marker in _FRONT_STRONG_MARKERS)
    has_weak = any(marker in normalized for marker in _FRONT_WEAK_MARKERS)
    has_country = any(marker in normalized for marker in _FRONT_COUNTRY_MARKERS)
    return has_strong or (has_weak and has_country)


def _clean_name_value(line: str) -> str | None:
    """Qiymat qatoridagi eng uzun harflar ketma-ketligini oladi (OCR chetlab
    qo'ygan chiziqcha/nuqta kabi shovqinlarni tashlab yuborish uchun).

    Familiya/ism uchun bitta so'zdan iborat bo'lgani sababli mos keladi."""
    words = re.findall(r"[A-Z]{2,}", line)
    if not words:
        return None
    return max(words, key=len)


def _clean_patronymic_value(line: str) -> str | None:
    """Otasining ismi qatoridagi HAMMA harf so'zlarini o'z tartibida
    birlashtiradi. Otasining ismi ko'pincha ikki so'zdan iborat bo'ladi
    (masalan "ANVAR OGLI" — "Anvar o'g'li"), shu sababli faqat eng uzun
    so'zni olish (``_clean_name_value`` qiladigani) "OGLI"/"QIZI" kabi
    qisqaroq, lekin muhim so'zni tashlab yuborishi yoki uni asosiy ism deb
    xato olib, haqiqiy ismni yo'qotishi mumkin."""
    words = re.findall(r"[A-Z]{2,}", line)
    if not words:
        return None
    return " ".join(words)


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
    patronymic = _clean_patronymic_value(values["patronymic"]) if "patronymic" in values else None

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

    def attempt(text: str) -> dict | None:
        nonlocal template_matched
        if not _matches_front_template(text):
            return None
        template_matched = True
        return _extract_front_fields(text)

    last_field_error: PassportOCRError | None = None
    for degrees in _rotations_to_try(raw_image):
        rotated = raw_image.rotate(-degrees, expand=True) if degrees else raw_image
        try:
            result = _try_ocr_combos(rotated, whitelist=None, attempt=attempt)
        except PassportOCRError as exc:
            last_field_error = exc
            continue
        if result is not None:
            return result

    if not template_matched:
        raise PassportOCRError("Old tomon rasmi namunaga (SHAXS GUVOHNOMASI old tomoni) mos kelmadi.")
    raise last_field_error


def extract_back_data(back_file) -> dict:
    """Orqa tomondagi MRZ'dan checksum bilan tasdiqlangan barcha maydonlarni
    (karta raqami, tug'ilgan sana, PNFL) va ism-familiyani oladi.

    MRZ OCR-B shrifti bilan bosilgan bo'lib, kartaning bezakli old tomon
    yozuvidan ancha barqaror o'qiladi, shu ustiga raqamli maydonlar checksum
    bilan tekshiriladi — shu sababli bu funksiya asosiy ma'lumot manbai
    hisoblanadi, old tomon esa faqat ism topilmaganda zaxira sifatida
    ishlatiladi (bu esa Tesseract chaqiruvlar sonini, demak kutish vaqtini,
    ko'p hollarda yarmiga qisqartiradi).
    """
    raw_image = _load_raw_image(back_file)

    last_error: PassportOCRError | None = None
    for degrees in _rotations_to_try(raw_image):
        rotated = raw_image.rotate(-degrees, expand=True) if degrees else raw_image
        try:
            return _try_ocr_combos(rotated, whitelist=_MRZ_CHARSET, attempt=_parse_mrz_full)
        except PassportOCRError as exc:
            last_error = exc

    raise last_error


def extract_passport_data(front_file, back_file) -> dict:
    """Orqa tomon MRZ'sini asosiy manba sifatida, old tomonni esa faqat ism
    MRZ'da topilmagan hollarda zaxira sifatida ishlatib, xodim ma'lumotlarini
    oladi (qarang: ``extract_back_data``)."""
    passport_data = extract_back_data(back_file)

    if not passport_data["full_name"]:
        front_data = extract_front_data(front_file)
        passport_data["full_name"] = front_data["full_name"]

    return passport_data
