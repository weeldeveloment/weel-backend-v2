from shared.raw.choices import ChoiceEnum


class Currency(ChoiceEnum):
    USD = "USD"
    UZS = "UZS"


Currency.choices = [
    (Currency.USD.value, "US Dollar"),
    (Currency.UZS.value, "Uzbekistan So'm"),
]
