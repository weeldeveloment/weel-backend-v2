"""``apps.b2b.passport_ocr`` ning OCR'siz (sof matn ustida ishlaydigan)
qismlari uchun testlar: MRZ'ni ajratish va check-digit tekshiruvi, old
tomon yorliqlarini topish, ovoz berish va yakuniy natijani yig'ish.

Bu yerdagi MRZ va old tomon matnlari haqiqiy kartalarning OCR chiqishidan
olingan (shu jumladan OCR'ning odatiy xatolari bilan), shuning uchun
testlar haqiqiy nosozliklarni ushlaydi."""
from datetime import date

import pytest

from apps.b2b.passport_ocr import (
    PassportOCRError,
    _build_result,
    _collect_front_fields,
    _collect_mrz,
    _consolidate_prefixes,
    _Evidence,
    _find_mrz_lines,
    _mrz_composite_ok,
    _normalize_patronymic,
    _parse_mrz_name,
)
from collections import Counter

# Haqiqiy karta (SETDAROV ABBOS, PNFL 51309076810024) orqa tomonining MRZ'si.
MRZ_TEXT = "\n".join(
    [
        "IUUZBAD4779438751309076810024<",
        "0709130M3310045UZBTRK<<<<<<<<2",
        "SETDAROV<<ABBOS<<<<<<<<<<<<<<<",
    ]
)
# Xuddi shu kartaning boshqa o'qishi: PNFL'ning oxirgi raqami xato
# ("...24" o'rniga "...25"). Faqat composite check shuni ajratib beradi.
MRZ_TEXT_BAD_PINFL = MRZ_TEXT.replace("51309076810024", "51309076810025")

FRONT_TEXT = "\n".join(
    [
        "O'ZBEKISTON RESPUBLIKASI",
        "SHAXS GUVOHNOMASI",
        "Familiyasi / Surname",
        "SETDAROV",
        "Ismi / Given name(s)",
        "ABBOS",
        "Otasining ismi / Patronymic",
        "SHUXRATOVICH",
        "Tug'ilgan sanasi / Date of birth",
        "13.09.2007",
        "Berilgan sanasi / Date of issue",
        "05.10.2023",
        "Amal qilish muddati / Date of expiry",
        "04.10.2033",
        "Karta raqami / Card number",
        "AD4779438",
    ]
)


def _front_evidence(text=FRONT_TEXT, times=2):
    evidence = _Evidence()
    for _ in range(times):
        _collect_front_fields(text, evidence)
    return evidence


def _back_evidence(text=MRZ_TEXT):
    evidence = _Evidence()
    _collect_mrz(text, evidence)
    return evidence


def test_mrz_lines_and_check_digits_of_a_real_card():
    line1, line2, line3 = _find_mrz_lines(MRZ_TEXT)
    assert line1.startswith("IUUZB")
    assert _mrz_composite_ok(line1, line2)
    assert _parse_mrz_name(line3) == ("SETDAROV", "ABBOS", None)


def test_mrz_composite_check_rejects_a_single_misread_pinfl_digit():
    good = _find_mrz_lines(MRZ_TEXT)
    bad = _find_mrz_lines(MRZ_TEXT_BAD_PINFL)
    assert _mrz_composite_ok(*good[:2])
    assert not _mrz_composite_ok(*bad[:2])


def test_pinfl_verified_by_composite_check_wins_over_more_frequent_misreads():
    """PNFL'ning o'z check raqami yo'q, shuning uchun composite check'dan
    o'tgan bitta o'qish undan ko'p marta takrorlangan xato o'qishni yengishi
    shart — aks holda eng ko'p uchragan (lekin xato) raqam tanlanardi."""
    back = _Evidence()
    _collect_mrz(MRZ_TEXT, back)
    for _ in range(4):
        _collect_mrz(MRZ_TEXT_BAD_PINFL, back)

    result = _build_result(_front_evidence(), back)
    assert result["passport_pinfl"] == "51309076810024"


def test_front_side_supplies_the_patronymic_missing_from_the_mrz():
    result = _build_result(_front_evidence(), _back_evidence())
    assert result["full_name"] == "SETDAROV ABBOS SHUXRATOVICH"
    assert result["date_of_birth"] == date(2007, 9, 13)
    assert result["passport_series"] == "AD4779438"
    assert result["passport_pinfl"] == "51309076810024"


def test_label_line_is_never_taken_as_a_name_even_when_ocr_garbles_it():
    """OCR "Patronymic" ni "Matronymic" deb o'qiganda yorliq qatorining
    o'zi ism sifatida olinib qolmasligi kerak."""
    text = FRONT_TEXT.replace("Otasining ismi / Patronymic", "Otasining ismi / Matronymic")
    evidence = _front_evidence(text)
    assert evidence.best("front_given_names") == "ABBOS"
    assert evidence.best("front_patronymic") == "SHUXRATOVICH"


def test_date_of_issue_is_not_mistaken_for_date_of_birth():
    evidence = _front_evidence()
    assert evidence.best("front_date_of_birth") == date(2007, 9, 13)


def test_ocr_noise_around_a_value_is_dropped():
    text = FRONT_TEXT.replace("SETDAROV", "FS SETDAROV -").replace("ABBOS", "ABB0S")
    evidence = _front_evidence(text)
    assert evidence.best("front_surname") == "SETDAROV"
    # Ismdagi "0" harf sifatida o'qiladi (ismlarda raqam bo'lmaydi).
    assert evidence.best("front_given_names") == "ABBOS"


def test_truncated_name_reads_are_counted_towards_the_full_one():
    counter = _consolidate_prefixes(Counter({"KOMI": 1, "KOMIL O'G'L": 1, "KOMIL O'G'LI": 1}))
    assert max(counter.items(), key=lambda item: item[1])[0] == "KOMIL O'G'LI"


def test_an_extra_fragment_is_not_merged_into_the_name():
    """"SETDAROV" va "SETDAROV S" — ikki xil o'qish; ikkinchisi shovqin va
    birinchisining ovozlarini o'ziga olmasligi kerak."""
    counter = _consolidate_prefixes(Counter({"SETDAROV": 3, "SETDAROV S": 1}))
    assert max(counter.items(), key=lambda item: item[1])[0] == "SETDAROV"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("KOMIL O'G'L", "KOMIL O'G'LI"),
        ("KOMIL O'G'LL", "KOMIL O'G'LI"),
        ("SODIQ QIZL", "SODIQ QIZI"),
        ("SHUXRATOVICH", "SHUXRATOVICH"),
    ],
)
def test_uzbek_patronymic_suffix_is_restored(raw, expected):
    assert _normalize_patronymic(raw) == expected


def test_mrz_filler_misread_as_letters_is_not_taken_as_a_name():
    surname, given_names, patronymic = _parse_mrz_name("SETDAROV<<ABBOS<KKKS<<<<<<<<<<")
    assert (surname, given_names, patronymic) == ("SETDAROV", "ABBOS", None)


def test_missing_pinfl_reports_which_field_could_not_be_read():
    with pytest.raises(PassportOCRError) as exc:
        _build_result(_front_evidence(), _Evidence())
    assert "PNFL" in str(exc.value)
