"""O'zbekiston biometrik shaxs guvohnomasi (ID karta) rasmlaridan ma'lumot olish.

Ma'lumot manbalari kartaning ikki tomoni o'rtasida shunday taqsimlangan:

* **Old tomon** — ism, familiya, otasining ismi (sharif), tug'ilgan sana va
  karta raqami. Sharif faqat shu yerda bo'ladi: orqa tomondagi MRZ'ning
  3-qatoriga (``FAMILIYA<<ISM``) otasining ismi ko'p hollarda umuman
  yozilmaydi, shu sababli sharifni MRZ'dan olib bo'lmaydi.
* **Orqa tomon** — PNFL (14 xonali shaxsiy raqam). U ikki joyda uchraydi:
  MRZ'ning 1-qatoridagi "optional" maydonda va kartada "Shaxsiy raqami /
  Personal number" yozuvi ostida oddiy shriftda. Ikkalasi ham o'qiladi va
  bir-biri bilan solishtiriladi.

MRZ format (ICAO 9303, TD1, 3 qator x 30 belgi):
  1-qator: hujjat kodi(2) + davlat(3) + hujjat raqami(9) + check(1) + optional(15)
  2-qator: tug'ilgan sana YYMMDD(6) + check(1) + jins(1) + amal muddati YYMMDD(6)
           + check(1) + fuqarolik(3) + optional(11) + composite check(1)
  3-qator: FAMILIYA<<ISM<<<<<<<<<<<<<<<<
O'zbekiston ID kartasida 1-qatordagi 15 xonali optional maydon PNFL'ni saqlaydi.

Aniqlik uchun ikkita asosiy usul ishlatiladi:

1. **Check-digit tekshiruvi.** MRZ'dagi hujjat raqami, tug'ilgan sana va amal
   muddati o'z check raqamiga ega, PNFL esa 2-qator oxiridagi *composite*
   check raqami bilan qoplanadi. Ilgari composite check hisobga olinmagani
   uchun bitta xato o'qilgan raqamli PNFL (masalan ...63 o'rniga ...65)
   jimgina qabul qilinardi — endi bunday o'qish past ball oladi va
   tekshiruvdan o'tgan variant tanlanadi.
2. **Ovoz berish (voting).** Bitta rasm bir necha marta — turli burilish,
   threshold va segmentatsiya rejimida — o'qiladi va har bir maydon uchun
   eng ko'p (va eng ishonchli) tasdiqlangan qiymat tanlanadi. Shu sababli
   bitta shovqinli o'qish natijani buzmaydi.

Tezlik: har bir OCR chaqiruvi alohida jarayonga chiqqani uchun ular haqiqiy
parallel bajariladi (GIL to'sqinlik qilmaydi). Ikkala rasm ham BIR pulda,
bir vaqtda qayta ishlanadi; avval kartaning burilishi arzon sinov o'qishi
bilan aniqlanadi, so'ng faqat o'sha burilishda to'liq o'qishlar ketadi.
Natijada odatiy holatda ikki bosqich yetarli va kutish vaqti chaqiruvlar
soniga emas, mashinaning yadrolar soniga bog'liq bo'ladi.
"""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from difflib import SequenceMatcher

import pytesseract
from PIL import Image, ImageOps

# Tesseract o'zi ham OpenMP orqali ko'p oqimda ishlaydi. Biz o'nlab
# chaqiruvni bir vaqtda ishga tushirganimiz uchun bu ikki darajali
# parallellik CPU'ni ortiqcha band qilib, aksincha sekinlashtiradi
# (Tesseract hujjatlarining o'zi ham parallel chaqiruvlarda
# OMP_THREAD_LIMIT=1 ni tavsiya qiladi). Parallellikni o'zimiz boshqaramiz.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

# Har bir OCR chaqiruvi alohida OS jarayoniga chiqadi, shuning uchun
# oqimlar GIL bilan cheklanmaydi — parallellik real. Ish hajmi
# (rasm x burilish x threshold x psm) mashinaning yadrolar soniga
# moslashtiriladi.
_OCR_WORKERS = max(4, min(32, os.cpu_count() or 4))


class PassportOCRError(Exception):
    """Passport rasmini shablon bo'yicha tekshirish yoki undan ma'lumot o'qishda xatolik."""


_MRZ_CHARSET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
_MRZ_LINE_RE = re.compile(r"^[A-Z0-9<]{28,31}$")
_MRZ_WEIGHTS = (7, 3, 1)

_FRONT_STRONG_MARKERS = ("GUVOHNOMASI",)
_FRONT_WEAK_MARKERS = ("IDENTITY CARD",)
_FRONT_COUNTRY_MARKERS = ("UZBEKISTON", "OZBEKISTON")

# Rasm kamera bilan olinganda karta ko'pincha tekis emas, hatto 90/180 gradusga
# burilgan holda tushishi mumkin — EXIF faqat qurilma sensorining aylanishini
# to'g'rilaydi, kartaning qo'lda qanday tutilganini emas. Shu sababli barcha
# asosiy burilishlarni sinab ko'ramiz (odatda birinchisi — OSD taxmini —
# yetarli bo'ladi).
_ROTATIONS = (0, 90, 180, 270)
_PSM_MODES = (6, 11, 4)
_MRZ_PSM_MODES = (6, 11)
# None = faqat autocontrast. Kartadagi guilloche/gologramma fon naqshlari OCR
# matnini shovqinlashtiradi — qattiq threshold (binarizatsiya) fonni bosib,
# qalin qora matnni ajratib beradi, lekin yorug'lik sharoitiga qarab optimal
# qiymat farq qilishi mumkin, shuning uchun bir nechtasi sinab ko'riladi.
_THRESHOLDS = (None, 100, 120, 140)
# Orqa tomon MRZ whitelist bilan o'qiladi, lekin kartada PNFL "Shaxsiy
# raqami" yozuvi ostida oddiy shriftda ham bor — uni ko'rish uchun bir
# nechta whitelist'siz o'qish ham qo'shiladi (ikkinchi, mustaqil manba).
_BACK_PLAIN_THRESHOLDS = (None, 120)

# Tesseract'ning aniqligi bu o'lchamdan yuqorida sezilarli oshmaydi, lekin
# ishlash vaqti taxminan rasm yuzasiga proportsional o'sadi. Zamonaviy
# telefon kameralari 3000-4000px kenglikdagi rasm berishi odatiy holat.
# Rasm hajmi BIR MARTA, yuklanganda normallashtiriladi. O'lchov kenglik
# bo'yicha emas, UZUN tomon bo'yicha olinadi: 90 gradusga burilgan
# (portret) suratda karta rasmning balandligi bo'ylab yotadi, shuning
# uchun kenglikni 1800px ga keltirish rasmni keraksiz kattalashtirib,
# har bir chaqiruvni sekinlashtirardi.
_OCR_TARGET_LONG_SIDE = 1800

# Birinchi bosqichda natija topilgan bo'lsa, boshqa burilishlarni sinash
# foydasiz (karta qaysi burilishda o'qilganini bilamiz) — buning o'rniga
# xuddi shu burilishda qo'shimcha, agressivroq threshold/segmentatsiya
# variantlari o'qiladi. Bu ham tezroq, ham foydaliroq.
_EXTRA_THRESHOLDS = (80, 160, 190)
_EXTRA_PSM_MODES = (6, 11)

_APOSTROPHES = re.compile(r"[’'`´ʻʼ]")
_NAME_WORD_RE = re.compile(r"[A-Z][A-Z']+")
# Ismlarda raqam bo'lmaydi, lekin OCR shovqinli suratda harfni raqam deb
# o'qiydi (masalan "FAYYOZ" -> "FAYY0Z"). Ism maydonlarida bu almashtirish
# xavfsiz va bir xil ismning turli o'qishlarini bitta variantga birlashtirib,
# ovoz berishni kuchaytiradi.
_NAME_DIGIT_MAP = str.maketrans({"0": "O", "1": "I", "2": "Z", "5": "S", "6": "G", "8": "B"})
_DATE_RE = re.compile(r"(\d{2})[.,\-/ ](\d{2})[.,\-/ ](\d{4})")
_CARD_NUMBER_RE = re.compile(r"([A-Z]{2}\d{7})")
_PINFL_RE = re.compile(r"(?<!\d)(\d{14})(?!\d)")

# Old tomondagi maydon yorliqlari. Kartada har bir yorliq "O'zbekcha /
# Inglizcha" ko'rinishida bo'ladi va qiymat undan keyingi qatorda turadi.
_FRONT_LABELS = {
    "surname": ("FAMILIYASI", "SURNAME"),
    "given_names": ("ISMI", "GIVEN NAME(S)", "GIVEN NAMES", "GIVEN NAME"),
    "patronymic": ("OTASINING ISMI", "PATRONYMIC"),
    "date_of_birth": ("TUGILGAN SANASI", "DATE OF BIRTH"),
    "card_number": ("KARTA RAQAMI", "CARD NUMBER"),
}
# Qidirilayotgan maydonga tegishli bo'lmagan, lekin QIYMAT deb xato
# olinmasligi kerak bo'lgan yorliqlar. Ilgari yorliq qatori faqat aniq
# kalit so'z bo'yicha aniqlanardi va OCR "PATRONYMIC" ni "MATRONYMIC" deb
# o'qishi bilan yorliq qatorining o'zi ism sifatida olinardi.
_OTHER_LABELS = (
    "OZBEKISTON RESPUBLIKASI",
    "SHAXS GUVOHNOMASI",
    "REPUBLIC OF UZBEKISTAN",
    "IDENTITY CARD",
    "BERILGAN SANASI",
    "DATE OF ISSUE",
    "AMAL QILISH MUDDATI",
    "DATE OF EXPIRY",
    "JINSI",
    "SEX",
    "FUQAROLIGI",
    "CITIZENSHIP",
    "IMZOSI",
    "SIGNATURE",
    "SHAXSIY RAQAMI",
    "PERSONAL NUMBER",
    "TUGILGAN JOYI",
    "PLACE OF BIRTH",
    "BERILGAN JOYI",
    "PLACE OF ISSUE",
)
_ALL_LABEL_PHRASES = tuple(
    phrase for phrases in _FRONT_LABELS.values() for phrase in phrases
) + _OTHER_LABELS
# OCR bitta-ikkita harfni almashtirib yuborishi odatiy hol, shuning uchun
# yorliqlar aniq emas, o'xshashlik darajasi bo'yicha taqqoslanadi.
_LABEL_MATCH_RATIO = 0.75
# Yorliq topilgandan keyin qiymat shuncha qatordan uzoqda bo'lsa, u boshqa
# maydonga tegishli deb hisoblanadi (OCR qatorlarni chalkashtirgan holat).
_VALUE_SEARCH_DEPTH = 3

# Ovoz og'irliklari: check-digit bilan tasdiqlangan qiymat tasdiqlanmaganidan
# ustun turadi, ya'ni bitta tekshiruvdan o'tgan o'qish bir necha xato
# o'qishni yenga oladi.
_W_PLAIN = 1
_W_CHECKED = 4
_W_PINFL_COMPOSITE = 6
# PNFL ichida tug'ilgan sana takrorlanadi (2-7 raqamlar = DDMMYY) — bu
# check-digit'dan mustaqil, kuchli tasdiq.
_W_PINFL_DOB_MATCH = 3

# Qaysi maydon uchun natija "ishonchli" hisoblanadi (keyingi burilishlarni
# sinab ko'rish shart emas).
_CONFIDENT_WEIGHT = 2 * _W_PLAIN


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
    long_side = max(image.width, image.height)
    if long_side != _OCR_TARGET_LONG_SIDE:
        scale = _OCR_TARGET_LONG_SIDE / long_side
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.LANCZOS,
        )
    return image


def _ocr_image(image: Image.Image, *, whitelist: str | None = None, psm: int = 6) -> str:
    config = f"--psm {psm}"
    if whitelist:
        config += f" -c tessedit_char_whitelist={whitelist}"
    return pytesseract.image_to_string(image, config=config)


def _normalize(text: str) -> str:
    return _APOSTROPHES.sub("'", text).upper()


# --------------------------------------------------------------------------
# MRZ (orqa tomon)
# --------------------------------------------------------------------------


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


_REPEATED_CHAR_RE = re.compile(r"(.)\1{2,}")


def _is_mrz_name_part(part: str) -> bool:
    """MRZ'dagi bo'lak haqiqiy ism bo'lishi mumkinmi.

    Ismdan keyingi bo'sh joy MRZ'da '<' bilan to'ldiriladi va OCR bu
    belgilar ketma-ketligini ba'zan harf deb o'qiydi ("<<<<" -> "KKKS").
    Bunday bo'laklar bir xil harfning takrorlanishi bilan ajralib turadi."""
    return len(part) >= 3 and not _REPEATED_CHAR_RE.search(part)


def _parse_mrz_name(line3: str) -> tuple[str, str, str | None]:
    """3-qatordan FAMILIYA<<ISM<OTASINING_ISMI shablonini ajratadi.

    Otasining ismi ko'pincha ikki so'zdan iborat bo'ladi — masalan
    "ANVAR<OGLI" ("Anvar o'g'li") yoki "SODIQ<QIZI" ("Sodiq qizi") —
    shuning uchun ISM'dan keyingi qolgan HAMMA '<'-ajratilgan bo'laklar
    (faqat birinchisi emas) otasining ismiga qo'shib olinadi, aks holda
    "O'G'LI"/"QIZI" so'zi tashlanib, ism to'liqsiz chiqadi. Ko'p kartalarda
    esa MRZ'da sharif umuman bo'lmaydi — u holda u old tomondan olinadi."""
    stripped = line3.rstrip("<")
    surname_raw, _, rest = stripped.partition("<<")
    # OCR MRZ'ning bo'sh joyini to'ldiruvchi '<' belgilarini xato o'qib,
    # ismdan keyin bir-ikki harfli "bo'lak" qo'shib qo'yishi mumkin
    # ("SETDAROV<<ABBOS<S<<<"). Bunday parchalar ism deb olinmaydi.
    given_parts = [part for part in rest.split("<") if _is_mrz_name_part(part)]
    surname = " ".join(part for part in surname_raw.split("<") if _is_mrz_name_part(part))
    given_names = given_parts[0] if given_parts else ""
    patronymic = " ".join(given_parts[1:]) if len(given_parts) > 1 else None
    return surname, given_names, patronymic


def _find_mrz_lines(text: str) -> tuple[str, str, str | None] | None:
    """OCR matnidan MRZ'ning 1- va 2-qatorini (va bo'lsa 3-qatorini) topadi.

    3-qator (ism) majburiy emas: PNFL uchun faqat 1- va 2-qator kerak, va
    shovqinli suratda ismlar qatori o'qilmay qolishi PNFL'ni ham yo'qotib
    yuborishi kerak emas."""
    candidates = []
    for raw_line in text.splitlines():
        line = raw_line.strip().upper().replace(" ", "")
        if _MRZ_LINE_RE.match(line):
            candidates.append(line.ljust(30, "<")[:30])
    for i in range(len(candidates) - 1):
        l1, l2 = candidates[i], candidates[i + 1]
        if "UZB" not in l1[:8] or not l2[:6].isdigit():
            continue
        l3 = candidates[i + 2] if i + 2 < len(candidates) else None
        if l3 is not None and "<<" not in l3:
            l3 = None
        return l1, l2, l3
    return None


def _mrz_composite_ok(line1: str, line2: str) -> bool:
    """TD1'da 2-qator oxiridagi composite check 1-qatorning 6-30 pozitsiyalarini
    ham qoplaydi — ya'ni PNFL'ni ham. PNFL'ning o'z check raqami yo'q, shuning
    uchun uni tekshirishning yagona yo'li shu."""
    composite = line1[5:30] + line2[0:7] + line2[8:15] + line2[18:29]
    return line2[29].isdigit() and str(_check_digit(composite)) == line2[29]


def _mrz_date(field: str) -> date | None:
    if not field.isdigit():
        return None
    yy, mm, dd = int(field[0:2]), int(field[2:4]), int(field[4:6])
    try:
        return date(_mrz_year_to_full(yy), mm, dd)
    except ValueError:
        return None


def _collect_mrz(text: str, evidence: _Evidence) -> None:
    """MRZ topilsa, har bir maydonni o'z ishonch og'irligi bilan qayd etadi.

    Ilgari bitta maydon (masalan hujjat raqami) check'dan o'tmasa, BUTUN
    o'qish rad etilardi va PNFL ham yo'qolardi. Endi har bir maydon
    mustaqil baholanadi."""
    lines = _find_mrz_lines(text)
    if lines is None:
        return
    line1, line2, line3 = lines
    evidence.mrz_seen = True
    composite_ok = _mrz_composite_ok(line1, line2)

    document_field = line1[5:14]
    card_match = _CARD_NUMBER_RE.match(document_field)
    if card_match:
        document_ok = line1[14].isdigit() and str(_check_digit(document_field)) == line1[14]
        evidence.add("card_number", card_match.group(1), _W_CHECKED if document_ok else _W_PLAIN)

    pinfl = line1[15:30].replace("<", "")
    if re.fullmatch(r"\d{14}", pinfl):
        evidence.add("pinfl", pinfl, _W_PINFL_COMPOSITE if composite_ok else _W_PLAIN)

    dob = _mrz_date(line2[0:6])
    if dob:
        dob_ok = line2[6].isdigit() and str(_check_digit(line2[0:6])) == line2[6]
        evidence.add("date_of_birth", dob, _W_CHECKED if dob_ok else _W_PLAIN)

    if line3:
        surname, given_names, patronymic = _parse_mrz_name(line3)
        # MRZ ismlari check-digit bilan qoplanmaydi, shuning uchun ular
        # har doim eng past og'irlikda va old tomondan keyin turadi.
        evidence.add("mrz_surname", surname.translate(_NAME_DIGIT_MAP), _W_PLAIN)
        evidence.add("mrz_given_names", given_names.translate(_NAME_DIGIT_MAP), _W_PLAIN)
        if patronymic:
            evidence.add(
                "mrz_patronymic",
                _normalize_patronymic(patronymic.translate(_NAME_DIGIT_MAP)),
                _W_PLAIN,
            )


def _collect_printed_pinfl(text: str, evidence: _Evidence) -> None:
    """Orqa tomonda "Shaxsiy raqami / Personal number" ostida oddiy shriftda
    bosilgan PNFL — MRZ'dan butunlay mustaqil ikkinchi manba."""
    for match in _PINFL_RE.finditer(_normalize(text).replace(" ", "")):
        evidence.add("pinfl", match.group(1), _W_PLAIN)


# --------------------------------------------------------------------------
# Old tomon
# --------------------------------------------------------------------------


def _matches_front_template(text: str) -> bool:
    normalized = _normalize(text)
    has_strong = any(marker in normalized for marker in _FRONT_STRONG_MARKERS)
    has_weak = any(marker in normalized for marker in _FRONT_WEAK_MARKERS)
    has_country = any(marker in normalized for marker in _FRONT_COUNTRY_MARKERS)
    return has_strong or (has_weak and has_country)


def _label_similarity(chunk: str, phrase: str) -> float:
    return SequenceMatcher(None, chunk, phrase).ratio()


def _classify_line(line: str) -> str | None:
    """Qator yorliqmi (va qaysi maydonniki) yoki qiymatmi — shuni aniqlaydi.

    Kartadagi yorliqlar "Familiyasi / Surname" ko'rinishida, ya'ni "/" bilan
    ajratilgan ikki tilda. Har bo'lak alohida, o'xshashlik bo'yicha
    taqqoslanadi — bu OCR bir-ikki harfni buzib o'qiganda ham ishlaydi.
    Natija: maydon nomi, "other" (begona yorliq) yoki None (qiymat qatori)."""
    chunks = [chunk.strip() for chunk in line.split("/") if chunk.strip()]
    best_field: str | None = None
    best_ratio = _LABEL_MATCH_RATIO
    for chunk in chunks:
        for field, phrases in _FRONT_LABELS.items():
            for phrase in phrases:
                ratio = _label_similarity(chunk, phrase)
                if ratio > best_ratio:
                    best_ratio, best_field = ratio, field
        for phrase in _OTHER_LABELS:
            ratio = _label_similarity(chunk, phrase)
            if ratio > best_ratio:
                best_ratio, best_field = ratio, "other"
    return best_field


def _looks_like_label(line: str) -> bool:
    """Qiymat sifatida olinayotgan qator aslida yorliq bo'lib qolmasligi uchun."""
    return any(
        _label_similarity(line, phrase) >= _LABEL_MATCH_RATIO for phrase in _ALL_LABEL_PHRASES
    )


def _name_value(line: str) -> str | None:
    """Qiymat qatoridan ism/familiyani ajratadi.

    Kartada ism qiymati bir (familiya, ism) yoki ikki-uch (sharif: "KOMIL
    O'G'LI") so'zdan iborat bo'ladi. OCR qatorga qo'shib qo'ygan qisqa
    shovqin bo'laklari ("BE", "VA PS" kabi) tashlab yuboriladi — lekin
    qatorda faqat qisqa bo'laklar bo'lsa, eng uzuni saqlanadi."""
    if _looks_like_label(line):
        return None
    words = _NAME_WORD_RE.findall(line.translate(_NAME_DIGIT_MAP))
    words = [w for w in words if not _looks_like_label(w)]
    if not words:
        return None
    long_words = [w for w in words if len(w.replace("'", "")) >= 3]
    words = long_words if long_words else [max(words, key=len)]
    return " ".join(words[:3])


# O'zbek sharifi deyarli har doim "... o'g'li" yoki "... qizi" bilan
# tugaydi. Bu qo'shimcha kartada kichik shriftda, apostroflar bilan
# yozilgani uchun OCR uni tez-tez kesib ("O'G'L") yoki oxirgi harfini
# almashtirib ("O'G'LL") o'qiydi. Ma'lum qo'shimchani tiklash bir xil
# sharifning turli o'qishlarini birlashtiradi va natijani to'liq qiladi.
_PATRONYMIC_SUFFIXES = ("O'G'LI", "QIZI")
_PATRONYMIC_SUFFIX_RATIO = 0.7
_PATRONYMIC_SUFFIX_MAX_LEN = 6


def _normalize_patronymic(value: str | None) -> str | None:
    if not value:
        return None
    words = value.split()
    if len(words) < 2:
        return value
    bare = words[-1].replace("'", "")
    if len(bare) <= _PATRONYMIC_SUFFIX_MAX_LEN:
        for suffix in _PATRONYMIC_SUFFIXES:
            if _label_similarity(bare, suffix.replace("'", "")) >= _PATRONYMIC_SUFFIX_RATIO:
                words[-1] = suffix
                break
    return " ".join(words)


def _consolidate_prefixes(counter: Counter) -> Counter:
    """Ismning kesilib qolgan variantlari ovozini to'liq variantga qo'shadi.

    OCR ism oxirini yeb qo'yishi ("KOMIL O'G'LI" o'rniga "KOMI") harf
    qo'shib qo'yishidan ancha ko'p uchraydi. Shuning uchun qisqa variant
    uzunroq variantning boshi bo'lsa, ular bitta o'qish deb qaraladi va
    ovozlar to'liq variantga yoziladi — aks holda hammasi bittadan ovoz
    olib, tenglikda tasodifiy (ko'pincha kesilgan) variant tanlanardi."""
    values = sorted(counter, key=len)
    merged = Counter(counter)
    for index, shorter in enumerate(values):
        for longer in values[index + 1:]:
            # Faqat SO'Z ichida kesilgan variantlar birlashtiriladi:
            # "SETDAROV" va "SETDAROV S" — ikki xil o'qish (ikkinchisida
            # ortiqcha bo'lak bor), ularni birlashtirish shovqinni g'olib
            # qilib qo'yardi.
            if longer.startswith(shorter) and not longer[len(shorter)].isspace():
                merged[longer] += counter[shorter]
    return merged


def _date_value(line: str) -> date | None:
    match = _DATE_RE.search(line)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        value = date(year, month, day)
    except ValueError:
        return None
    # Tug'ilgan sana kelajakda bo'lolmaydi va kartadagi "berilgan sanasi"
    # bilan chalkashib ketmasligi uchun oqilona oraliq bilan cheklanadi.
    if not (date(1900, 1, 1) <= value <= date.today()):
        return None
    return value


def _card_number_value(line: str) -> str | None:
    match = _CARD_NUMBER_RE.search(line.replace(" ", ""))
    return match.group(1) if match else None


_FRONT_VALUE_PARSERS = {
    "surname": _name_value,
    "given_names": _name_value,
    "patronymic": _name_value,
    "date_of_birth": _date_value,
    "card_number": _card_number_value,
}


def _collect_front_fields(text: str, evidence: _Evidence) -> None:
    """Yorliq qatorini topib, undan keyingi mos qiymat qatorini oladi.

    Har bir maydon o'z tekshiruvchisiga ega (sana — sana ko'rinishida,
    karta raqami — AA1234567 shablonida), shuning uchun OCR ortiqcha qator
    qo'shib qo'ysa ham qiymat to'g'ri topiladi."""
    if _matches_front_template(text):
        evidence.front_template = True

    lines = [_normalize(ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    classes = [_classify_line(ln) for ln in lines]

    for index, field in enumerate(classes):
        if field is None or field == "other":
            continue
        parser = _FRONT_VALUE_PARSERS[field]
        for offset in range(1, _VALUE_SEARCH_DEPTH + 1):
            j = index + offset
            if j >= len(lines):
                break
            if classes[j] is not None:  # boshqa yorliq — qiymat emas
                continue
            value = parser(lines[j])
            if value is not None:
                if field == "patronymic":
                    value = _normalize_patronymic(value)
                evidence.add(f"front_{field}", value, _W_PLAIN)
                break


# --------------------------------------------------------------------------
# Ovoz to'plash va OCR quvuri
# --------------------------------------------------------------------------


_NAME_FIELDS = frozenset(
    f"{prefix}_{part}"
    for prefix in ("front", "mrz")
    for part in ("surname", "given_names", "patronymic")
)


def _is_name_field(field: str) -> bool:
    return field in _NAME_FIELDS


class _Evidence:
    """Bitta rasmning barcha o'qishlaridan yig'ilgan maydon-qiymat ovozlari."""

    def __init__(self) -> None:
        self.votes: dict[str, Counter] = defaultdict(Counter)
        self.front_template = False
        self.mrz_seen = False

    def add(self, field: str, value, weight: int) -> None:
        if value:
            self.votes[field][value] += weight

    def counter(self, field: str) -> Counter:
        counter = self.votes.get(field)
        if not counter:
            return Counter()
        return _consolidate_prefixes(counter) if _is_name_field(field) else counter

    def weight(self, field: str) -> int:
        counter = self.counter(field)
        return max(counter.values()) if counter else 0

    def best(self, field: str):
        counter = self.counter(field)
        if not counter:
            return None
        return max(counter.items(), key=lambda item: item[1])[0]


def _ocr_tasks(side: str, *, extra: bool = False) -> list[tuple[int | None, int, str | None]]:
    """(threshold, psm, whitelist) kombinatsiyalari ro'yxati."""
    if side == "back":
        if extra:
            tasks = [(t, psm, _MRZ_CHARSET) for t in _EXTRA_THRESHOLDS for psm in _EXTRA_PSM_MODES]
            return tasks + [(t, 6, None) for t in _EXTRA_THRESHOLDS]
        tasks = [(t, psm, _MRZ_CHARSET) for t in _THRESHOLDS for psm in _MRZ_PSM_MODES]
        return tasks + [(t, psm, None) for t in _BACK_PLAIN_THRESHOLDS for psm in _MRZ_PSM_MODES]
    if extra:
        tasks = [(t, psm, None) for t in _EXTRA_THRESHOLDS for psm in _EXTRA_PSM_MODES]
        return tasks + [(t, 3, None) for t in _BACK_PLAIN_THRESHOLDS]
    return [(t, psm, None) for t in _THRESHOLDS for psm in _PSM_MODES]


def _collect_from_text(text: str, whitelist: str | None, evidence: _Evidence) -> None:
    """Har bir o'qishni HAR IKKI parser orqali o'tkazadi.

    Bu qo'shimcha OCR xarajatisiz beriladi va foydalanuvchi rasmlarni
    o'rin almashtirib yuklagan holatni ham o'zi hal qiladi: MRZ qaysi
    rasmda bo'lsa, o'sha yerdan topiladi."""
    _collect_mrz(text, evidence)
    if whitelist is None:
        _collect_front_fields(text, evidence)
        _collect_printed_pinfl(text, evidence)


# Kartaning burilishini aniqlash uchun to'rtala burilish KICHRAYTIRILGAN
# nusxada bir martadan o'qib ko'riladi va qaysi biri kartaga o'xshash matn
# bergani baholanadi. Kichraytirilgan rasmda bu chaqiruvlar bir necha
# barobar tez, ustiga ular ham parallel ketadi — natijada bu bosqich
# taxminan bitta to'liq o'lchamli chaqiruv vaqtini oladi.
#
# Ilgari bu ish Tesseract OSD (``--psm 0``) ga topshirilgan edi, lekin u
# bezakli, fon naqshli kartada ko'pincha past ishonch bilan noto'g'ri
# burchak qaytaradi (o'lchangan holatlarda "orientation confidence" 0.43
# gacha tushgan) — natijada quvur noto'g'ri burilishdan boshlab, barcha
# bosqichlarni bekorga bajarardi. Bu yerdagi sinov esa bevosita bizga
# kerak savolga javob beradi: karta qaysi burilishda O'QILADI.
_PROBE_LONG_SIDE = 900
_PROBE_MATCH_SCORE = 10
_MRZ_LIKE_LINE_RE = re.compile(r"^[A-Z0-9<]{20,}$")


def _probe_score(text: str, side: str) -> int:
    """Sinov o'qishi kartaga qanchalik o'xshaganini baholaydi."""
    if side == "back":
        score = _PROBE_MATCH_SCORE if _find_mrz_lines(text) else 0
        score += sum(
            1 for line in text.splitlines() if _MRZ_LIKE_LINE_RE.match(line.strip().replace(" ", ""))
        )
        return score + (5 if _PINFL_RE.search(text.replace(" ", "")) else 0)
    score = _PROBE_MATCH_SCORE if _matches_front_template(text) else 0
    lines = [_normalize(line).strip() for line in text.splitlines()]
    return score + sum(1 for line in lines if line and _classify_line(line))


class _Side:
    """Bitta rasm (old yoki orqa tomon) uchun bosqichma-bosqich OCR holati."""

    def __init__(self, image_file, side: str) -> None:
        self.side = side
        self.image = _load_raw_image(image_file)
        self.evidence = _Evidence()
        self.rotations: tuple[int, ...] = _ROTATIONS
        self._rotation_index = 0
        self._extra_done = False
        self._preprocessed: dict[tuple[int, int | None], Image.Image] = {}
        scale = _PROBE_LONG_SIDE / max(self.image.width, self.image.height)
        self._probe_image = (
            self.image.resize(
                (max(1, round(self.image.width * scale)), max(1, round(self.image.height * scale))),
                Image.LANCZOS,
            )
            if scale < 1
            else self.image
        )

    def probe(self, degrees: int) -> int:
        image = self._probe_image.rotate(-degrees, expand=True) if degrees else self._probe_image
        whitelist = _MRZ_CHARSET if self.side == "back" else None
        try:
            text = _ocr_image(_preprocess_for_ocr(image), whitelist=whitelist, psm=6)
        except pytesseract.TesseractError:
            return 0
        return _probe_score(text, self.side)

    def _prepared(self, degrees: int, threshold: int | None) -> Image.Image:
        key = (degrees, threshold)
        if key not in self._preprocessed:
            rotated = self.image.rotate(-degrees, expand=True) if degrees else self.image
            self._preprocessed[key] = _preprocess_for_ocr(rotated, threshold=threshold)
        return self._preprocessed[key]

    def found_anything(self) -> bool:
        """Karta shu burilishda umuman tanildimi (yorliqlar yoki MRZ)."""
        if self.side == "back":
            return self.evidence.mrz_seen or bool(self.evidence.votes.get("pinfl"))
        return self.evidence.front_template or bool(self.evidence.votes)

    def is_confident(self) -> bool:
        """Bu tomondan kutilayotgan ma'lumot ishonchli olindimi.

        Orqa tomon uchun — composite check'dan o'tgan PNFL; old tomon uchun
        — ism, familiya, sharif va tug'ilgan sana kamida ikki mustaqil
        o'qishda bir xil chiqqani. Karta raqami bu ro'yxatda yo'q: u
        orqa tomondagi MRZ'dan check-digit bilan tasdiqlangan holda
        keladi, shuning uchun uni old tomondan qayta-qayta izlash
        kutish vaqtini bekorga uzaytirardi."""
        evidence = self.evidence
        if self.side == "back":
            return evidence.weight("pinfl") >= _W_PINFL_COMPOSITE
        return all(
            evidence.weight(f"front_{field}") >= _CONFIDENT_WEIGHT
            for field in ("surname", "given_names", "patronymic", "date_of_birth")
        )

    def next_jobs(self) -> list:
        """Keyingi bosqichdagi OCR chaqiruvlari (bo'lmasa — bo'sh ro'yxat).

        Agar karta joriy burilishda tanilgan bo'lsa, boshqa burilishlarni
        sinash mantiqsiz — o'sha burilishda qo'shimcha threshold/rejimlar
        o'qiladi. Aks holda keyingi burilishga o'tiladi."""
        if self.is_confident():
            return []
        if self.found_anything():
            if self._extra_done:
                return []
            self._extra_done = True
            degrees, extra = self.rotations[self._rotation_index], True
        else:
            self._rotation_index += 1
            if self._rotation_index >= len(self.rotations):
                return []
            degrees, extra = self.rotations[self._rotation_index], False

        jobs = []
        for threshold, psm, whitelist in _ocr_tasks(self.side, extra=extra):
            image = self._prepared(degrees, threshold)
            jobs.append(
                lambda image=image, psm=psm, whitelist=whitelist: (
                    whitelist,
                    _ocr_image(image, whitelist=whitelist, psm=psm),
                )
            )
        return jobs

    def first_jobs(self) -> list:
        degrees = self.rotations[0]
        return [
            (
                lambda image=self._prepared(degrees, threshold), psm=psm, whitelist=whitelist: (
                    whitelist,
                    _ocr_image(image, whitelist=whitelist, psm=psm),
                )
            )
            for threshold, psm, whitelist in _ocr_tasks(self.side)
        ]


def _run_stage(pool: ThreadPoolExecutor, jobs_by_side: list[tuple[_Side, list]]) -> bool:
    """Bir bosqichdagi barcha chaqiruvlarni (ikkala tomon uchun ham) bir
    vaqtda bajaradi. Kutish vaqti chaqiruvlar soniga emas, mashinaning
    yadrolar soniga bog'liq bo'ladi."""
    futures = [(side, pool.submit(job)) for side, jobs in jobs_by_side for job in jobs]
    for side, future in futures:
        try:
            whitelist, text = future.result()
        except pytesseract.TesseractError:
            continue
        _collect_from_text(text, whitelist, side.evidence)
    return bool(futures)


def _rank_rotations(pool: ThreadPoolExecutor, sides: tuple[_Side, ...]) -> None:
    """Har bir rasm uchun burilishlarni "qaysi biri o'qildi" bo'yicha
    tartiblaydi. Ikkala rasmning to'rtala sinovi ham bir vaqtda ketadi."""
    futures = {
        (index, degrees): pool.submit(side.probe, degrees)
        for index, side in enumerate(sides)
        for degrees in _ROTATIONS
    }
    for index, side in enumerate(sides):
        scores = {degrees: futures[(index, degrees)].result() for degrees in _ROTATIONS}
        side.rotations = tuple(sorted(_ROTATIONS, key=lambda d: -scores[d]))


def _process_sides(sides: tuple[_Side, ...]) -> None:
    with ThreadPoolExecutor(max_workers=_OCR_WORKERS) as pool:
        _rank_rotations(pool, sides)
        _run_stage(pool, [(side, side.first_jobs()) for side in sides])
        while _run_stage(pool, [(side, side.next_jobs()) for side in sides]):
            pass


def _run_pipeline(front_file, back_file) -> tuple[_Side, _Side]:
    """Ikkala rasmni bitta pulda, bir vaqtda qayta ishlaydi."""
    front = _Side(front_file, "front")
    back = _Side(back_file, "back")
    _process_sides((front, back))
    return front, back


# --------------------------------------------------------------------------
# Natijani yig'ish
# --------------------------------------------------------------------------


def _combined(front: _Evidence, back: _Evidence, field: str) -> Counter:
    counter = Counter()
    counter.update(front.counter(field))
    counter.update(back.counter(field))
    return counter


def _pick_name(front: _Evidence, back: _Evidence, part: str) -> str | None:
    """Ism qismlari uchun manbalar ustuvorligi: old tomondagi bosma yozuv
    (sharif faqat shu yerda bor), so'ng MRZ (zaxira)."""
    for evidence, key in (
        (front, f"front_{part}"),
        (back, f"front_{part}"),
        (back, f"mrz_{part}"),
        (front, f"mrz_{part}"),
    ):
        value = evidence.best(key)
        if value:
            return value
    return None


def _pick_pinfl(front: _Evidence, back: _Evidence, date_of_birth: date | None) -> str | None:
    """PNFL: composite check'dan o'tgan MRZ o'qishi, bosma "Shaxsiy raqami"
    yozuvi va tug'ilgan sana mosligi — uchala dalil birga baholanadi.

    PNFL'ning 2-7 raqamlari tug'ilgan sanani DDMMYY ko'rinishida takrorlaydi,
    1-raqam esa jins/asrni bildiradi (1-6). Bu MRZ check'idan mustaqil
    tekshiruv bo'lgani uchun bitta raqami xato o'qilgan variantni ajratib
    beradi."""
    counter = _combined(front, back, "pinfl")
    if not counter:
        return None

    def score(item: tuple[str, int]) -> tuple[int, int]:
        value, weight = item
        bonus = 0
        if date_of_birth and value[1:7] == date_of_birth.strftime("%d%m%y"):
            bonus += _W_PINFL_DOB_MATCH
        if value[0] in "123456":
            bonus += 1
        return weight + bonus, weight

    return max(counter.items(), key=score)[0]


def _build_result(front: _Evidence, back: _Evidence) -> dict:
    date_of_birth = None
    dob_counter = _combined(front, back, "front_date_of_birth")
    dob_counter.update(_combined(front, back, "date_of_birth"))
    if dob_counter:
        date_of_birth = max(dob_counter.items(), key=lambda item: item[1])[0]

    card_counter = _combined(front, back, "front_card_number")
    card_counter.update(_combined(front, back, "card_number"))
    card_number = max(card_counter.items(), key=lambda item: item[1])[0] if card_counter else None

    surname = _pick_name(front, back, "surname")
    given_names = _pick_name(front, back, "given_names")
    patronymic = _pick_name(front, back, "patronymic")
    pinfl = _pick_pinfl(front, back, date_of_birth)

    missing = []
    if not pinfl:
        missing.append("PNFL (shaxsiy raqam)")
    if not surname or not given_names:
        missing.append("ism-familiya")
    if not date_of_birth:
        missing.append("tug'ilgan sana")
    if not card_number:
        missing.append("karta raqami")
    if missing:
        raise PassportOCRError(
            f"Rasmlardan {', '.join(missing)} aniqlanmadi. "
            "Kartaning old va orqa tomonini tekis, yorug' joyda, soya va "
            "yarqirashsiz qilib qayta suratga oling."
        )

    return {
        "full_name": " ".join(part for part in (surname, given_names, patronymic) if part),
        "date_of_birth": date_of_birth,
        "passport_series": card_number,
        "passport_pinfl": pinfl,
    }


def extract_passport_data(front_file, back_file) -> dict:
    """Old va orqa tomon rasmlaridan xodim ma'lumotlarini oladi.

    Ism, familiya, sharif, karta raqami va tug'ilgan sana asosan old
    tomondan; PNFL esa orqa tomondan olinadi (qarang: modul izohi)."""
    front, back = _run_pipeline(front_file, back_file)

    # Shablon tekshiruvi ikkala rasm bo'yicha birgalikda qilinadi: har bir
    # o'qish ham old tomon, ham MRZ parseridan o'tkazilgani uchun
    # foydalanuvchi rasmlarni o'rin almashtirib yuklagan bo'lsa ham
    # ma'lumot topiladi va bekorga xato qaytarilmaydi.
    if not (front.evidence.front_template or back.evidence.front_template):
        raise PassportOCRError(
            "Yuklangan rasm namunaga (O'zbekiston SHAXS GUVOHNOMASI) mos kelmadi. "
            "Kartaning old tomonini to'liq, tekis va yorug' joyda suratga oling."
        )
    if not (front.evidence.mrz_seen or back.evidence.mrz_seen):
        raise PassportOCRError(
            "Orqa tomondagi MRZ kodini o'qib bo'lmadi. Kartaning orqa tomonini "
            "(MRZ — pastdagi uzun kodli qatorlar ko'rinib turgan holda) "
            "aniqroq va yorug'roq sharoitda qayta yuklang."
        )

    return _build_result(front.evidence, back.evidence)


def extract_front_data(front_file) -> dict:
    """Faqat old tomon rasmidan ism-familiya, tug'ilgan sana va karta raqami."""
    side = _Side(front_file, "front")
    _process_sides((side,))

    evidence = side.evidence
    surname = _pick_name(evidence, _Evidence(), "surname")
    given_names = _pick_name(evidence, _Evidence(), "given_names")
    patronymic = _pick_name(evidence, _Evidence(), "patronymic")
    date_of_birth = evidence.best("front_date_of_birth") or evidence.best("date_of_birth")
    card_number = evidence.best("front_card_number") or evidence.best("card_number")
    if not surname or not given_names or not date_of_birth or not card_number:
        raise PassportOCRError(
            "Old tomon rasmidan ism-familiya/tug'ilgan sana/karta raqamini "
            "aniqlab bo'lmadi. Rasmni tekis va yorug' joyda qayta yuklang."
        )
    return {
        "full_name": " ".join(part for part in (surname, given_names, patronymic) if part),
        "date_of_birth": date_of_birth,
        "passport_series": card_number,
    }


def extract_back_data(back_file) -> dict:
    """Faqat orqa tomon rasmidan MRZ ma'lumotlari (asosiysi — PNFL)."""
    side = _Side(back_file, "back")
    _process_sides((side,))

    evidence = side.evidence
    date_of_birth = evidence.best("date_of_birth")
    pinfl = _pick_pinfl(_Evidence(), evidence, date_of_birth)
    if not pinfl:
        raise PassportOCRError("PNFL (14 xonali raqam) orqa tomondan aniqlanmadi.")
    surname = evidence.best("mrz_surname")
    given_names = evidence.best("mrz_given_names")
    patronymic = evidence.best("mrz_patronymic")
    full_name = ""
    if surname and given_names:
        full_name = " ".join(part for part in (surname, given_names, patronymic) if part)
    return {
        "full_name": full_name,
        "date_of_birth": date_of_birth,
        "passport_series": evidence.best("card_number"),
        "passport_pinfl": pinfl,
    }
