import os
import csv
from django.conf import settings

BIN_DATA = {}

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(settings.BASE_DIR, "bin-list-data.csv")


def load_bin_data():
    global BIN_DATA
    if BIN_DATA:
        return BIN_DATA

    try:
        with open(CSV_PATH, mode="r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                bin_code = row["BIN"].strip()
                brand = row["Brand"].strip()
                BIN_DATA[bin_code] = brand
    except FileNotFoundError:
        print("⚠️ BIN LIST CSV FILE NOT FOUND:", CSV_PATH)

    return BIN_DATA


def get_brand_for_card(card_number: str):
    if not card_number:
        return None
    clean_number = "".join(ch for ch in card_number if ch.isdigit())
    if len(clean_number) < 6:
        return None
    bin_code = clean_number[:6]
    return BIN_DATA.get(bin_code)
