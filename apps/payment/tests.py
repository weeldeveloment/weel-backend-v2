import logging
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch, MagicMock

from django.core.cache import cache
from django.db import IntegrityError
from django.db.utils import ProgrammingError
from django.test import TestCase
from django.utils import timezone

from rest_framework.exceptions import ValidationError

from users.models.clients import Client

from .choices import Currency
from .exchange_rate import exchange_rate, round_amount, to_uzs, to_usd
from .models import ExchangeRate, PlumTransaction, PlumTransactionStatus, PlumTransactionType
from .services import PlumAPIService, PlumAPIError
from .tasks import update_exchange_rate, _extract_usd_to_uzs_rate

logging.getLogger("payment.services").setLevel(logging.ERROR)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────


def make_client(**kwargs):
    defaults = {
        "first_name": "Test",
        "last_name": "Client",
        "phone_number": "+998901234567",
        "is_active": True,
    }
    defaults.update(kwargs)
    return Client.objects.create(**defaults)


# ──────────────────────────────────────────────
# 1. Model tests
# ──────────────────────────────────────────────


class PlumTransactionModelTests(TestCase):
    def test_create_plum_transaction(self):
        tx = PlumTransaction.objects.create(
            transaction_id="tx-1",
            hold_id="hold-1",
            amount=Decimal("200000"),
            type=PlumTransactionType.HOLD,
            status=PlumTransactionStatus.PENDING,
        )
        self.assertEqual(tx.transaction_id, "tx-1")
        self.assertEqual(tx.hold_id, "hold-1")
        self.assertEqual(tx.amount, Decimal("200000"))
        self.assertEqual(tx.status, PlumTransactionStatus.PENDING)

    def test_plum_transaction_str(self):
        tx = PlumTransaction.objects.create(
            transaction_id="tx-2",
            hold_id="hold-2",
            amount=Decimal("100000"),
            type=PlumTransactionType.CHARGE,
            status=PlumTransactionStatus.CHARGED,
        )
        s = str(tx)
        self.assertIn("tx-2", s)
        self.assertIn("hold-2", s)


class ExchangeRateModelTests(TestCase):
    def test_create_exchange_rate(self):
        today = timezone.localdate()
        rate = ExchangeRate.objects.create(currency=Currency.USD, rate=Decimal("12800.5"))
        self.assertEqual(rate.currency, Currency.USD)
        self.assertEqual(rate.date, today)

    def test_exchange_rate_unique_currency_date(self):
        today = timezone.localdate()
        ExchangeRate.objects.create(currency="USD", rate=Decimal("12800"))
        with self.assertRaises(IntegrityError):
            ExchangeRate.objects.create(currency="USD", rate=Decimal("12900"), date=today)

    def test_exchange_rate_str(self):
        r = ExchangeRate.objects.create(currency="UZS", rate=Decimal("1"))
        self.assertIn("UZS", str(r))
        self.assertIn("1", str(r))


# ──────────────────────────────────────────────
# 2. Exchange rate (cache + DB) — existing + new
# ──────────────────────────────────────────────


class ExchangeRateTests(TestCase):
    def setUp(self):
        cache.delete("usd_to_uzs_rate")

    def tearDown(self):
        cache.delete("usd_to_uzs_rate")

    def test_exchange_rate_uses_today_rate_when_present(self):
        ExchangeRate.objects.create(currency="USD", rate=Decimal("12650.500000"))

        rate = exchange_rate()

        self.assertEqual(rate, Decimal("12650.500000"))

    def test_exchange_rate_falls_back_to_latest_when_today_missing(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        yesterday_noon = timezone.make_aware(datetime.combine(yesterday, time(12, 0)))

        with patch("django.utils.timezone.now", return_value=yesterday_noon):
            ExchangeRate.objects.create(currency="USD", rate=Decimal("12600.000000"))

        rate = exchange_rate()

        self.assertEqual(rate, Decimal("12600.000000"))

    def test_exchange_rate_raises_when_no_data_exists(self):
        with self.assertRaises(ValidationError):
            exchange_rate()


class RoundAmountTests(TestCase):
    def test_round_down_when_remainder_under_5000(self):
        self.assertEqual(round_amount(Decimal("124999")), Decimal("120000"))
        self.assertEqual(round_amount(Decimal("120000")), Decimal("120000"))

    def test_round_up_when_remainder_5000_or_more(self):
        self.assertEqual(round_amount(Decimal("125001")), Decimal("130000"))
        self.assertEqual(round_amount(Decimal("129999")), Decimal("130000"))


class ToUzsToUsdTests(TestCase):
    @patch("payment.exchange_rate.exchange_rate")
    def test_to_uzs(self, mock_rate):
        mock_rate.return_value = Decimal("12800")
        self.assertEqual(to_uzs(Decimal("100")), Decimal("1280000"))  # 100 * 12800, round to 10k

    @patch("payment.exchange_rate.exchange_rate")
    def test_to_usd(self, mock_rate):
        mock_rate.return_value = Decimal("12800")
        self.assertEqual(to_usd(Decimal("12800")), Decimal("1"))


class UpdateExchangeRateTaskTests(TestCase):
    def setUp(self):
        cache.delete("usd_to_uzs_rate")

    def tearDown(self):
        cache.delete("usd_to_uzs_rate")

    @patch("payment.tasks.requests.get")
    def test_update_exchange_rate_uses_global_api_format(self, mock_get):
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "result": "success",
            "base_code": "USD",
            "rates": {"UZS": 12892.04},
        }
        mock_get.return_value = mock_response

        update_exchange_rate()

        record = ExchangeRate.objects.get(currency="USD", date=timezone.localdate())
        self.assertEqual(record.rate, Decimal("12892.04"))
        self.assertEqual(cache.get("usd_to_uzs_rate"), Decimal("12892.04"))

    @patch("payment.tasks.requests.get")
    def test_update_exchange_rate_keeps_legacy_cbu_compatibility(self, mock_get):
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = [
            {"Ccy": "USD", "Rate": "12770,55"},
            {"Ccy": "EUR", "Rate": "14000,10"},
        ]
        mock_get.return_value = mock_response

        update_exchange_rate()

        record = ExchangeRate.objects.get(currency="USD", date=timezone.localdate())
        self.assertEqual(record.rate, Decimal("12770.55"))

    @patch("payment.tasks.logger.exception")
    @patch("payment.tasks.ExchangeRate.objects.update_or_create")
    @patch("payment.tasks.requests.get")
    def test_update_exchange_rate_handles_missing_table_gracefully(
        self, mock_get, mock_update_or_create, mock_log_exception
    ):
        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {"result": "success", "rates": {"UZS": 12892.04}}
        mock_get.return_value = mock_response
        mock_update_or_create.side_effect = ProgrammingError("relation does not exist")

        # Should not raise; task should only log and continue.
        update_exchange_rate()

        self.assertEqual(cache.get("usd_to_uzs_rate"), Decimal("12892.04"))
        mock_log_exception.assert_called_once()


# ──────────────────────────────────────────────
# 4. tasks: _extract_usd_to_uzs_rate
# ──────────────────────────────────────────────


class ExtractUsdToUzsRateTests(TestCase):
    def test_extract_rates_key(self):
        self.assertEqual(
            _extract_usd_to_uzs_rate({"result": "success", "rates": {"UZS": 12892.04}}),
            Decimal("12892.04"),
        )

    def test_extract_conversion_rates_key(self):
        self.assertEqual(
            _extract_usd_to_uzs_rate({"conversion_rates": {"UZS": 13000}}),
            Decimal("13000"),
        )

    def test_extract_cbu_legacy_list_format(self):
        self.assertEqual(
            _extract_usd_to_uzs_rate([{"Ccy": "USD", "Rate": "12770,55"}]),
            Decimal("12770.55"),
        )

    def test_extract_raises_on_non_success_result(self):
        with self.assertRaises(ValueError) as ctx:
            _extract_usd_to_uzs_rate({"result": "error", "rates": {"UZS": 12800}})
        self.assertIn("non-success", str(ctx.exception))

    def test_extract_raises_on_success_false(self):
        with self.assertRaises(ValueError) as ctx:
            _extract_usd_to_uzs_rate({"success": False, "rates": {"UZS": 12800}})
        self.assertIn("success=false", str(ctx.exception))

    def test_extract_raises_when_uzs_not_found(self):
        with self.assertRaises(ValueError) as ctx:
            _extract_usd_to_uzs_rate({"result": "success", "rates": {"EUR": 1.1}})
        self.assertIn("not found", str(ctx.exception))


# ──────────────────────────────────────────────
# 5. PlumAPIService (mocked session)
# ──────────────────────────────────────────────


class PlumGetErrorMessageTests(TestCase):
    def test_error_message_from_error_message_dict(self):
        data = {"errorMessage": {"message": "Карты временно не принимаются"}}
        self.assertEqual(
            PlumAPIService._get_plum_error_message(data),
            "Карты временно не принимаются",
        )

    def test_error_message_from_error_code_dict(self):
        data = {"errorCode": {"message": "invalid_card"}}
        self.assertEqual(PlumAPIService._get_plum_error_message(data), "invalid_card")

    def test_error_message_fallback_to_error_key(self):
        self.assertEqual(
            PlumAPIService._get_plum_error_message({"error": "Unknown"}),
            "Unknown",
        )

    def test_error_message_empty_returns_plum_api_error(self):
        self.assertEqual(PlumAPIService._get_plum_error_message({}), "Plum API error")
        self.assertEqual(PlumAPIService._get_plum_error_message(None), "Plum API error")


def _make_plum_service():
    """PlumAPIService with dummy URLs so init does not depend on env."""
    return PlumAPIService(
        auth_token="test-token",
        api_get_all_user_cards="http://test/cards",
        api_add_user_card="http://test/add",
        api_verify_user_card="http://test/verify",
        api_remove_user_card="http://test/remove",
        api_create_hold="http://test/hold",
        api_charge_hold="http://test/charge",
        api_dismiss_hold="http://test/dismiss",
        api_confirm_hold="http://test/confirm",
        api_resend_otp="http://test/resend",
    )


class PlumAPIServiceTests(TestCase):
    def setUp(self):
        self.client = make_client()

    def test_get_client_cards_returns_empty_on_plum_error(self):
        service = _make_plum_service()
        service.session = MagicMock()
        service.session.get.return_value = MagicMock(
            ok=False,
            status_code=400,
            json=MagicMock(return_value={"errorMessage": {"message": "Bad request"}}),
        )
        with patch.object(PlumAPIService, "_handle_response", side_effect=PlumAPIError("Bad request", 400)):
            result = service.get_client_cards(self.client)
        self.assertEqual(result, {"result": {"cards": []}})

    def test_get_client_cards_success(self):
        service = _make_plum_service()
        service.session = MagicMock()
        service.session.get.return_value = MagicMock(
            ok=True,
            json=MagicMock(return_value={"result": {"cards": [{"id": "card1"}]}}),
        )
        result = service.get_client_cards(self.client)
        self.assertEqual(result["result"]["cards"][0]["id"], "card1")

    def test_save_transaction_creates_or_updates(self):
        service = _make_plum_service()
        result = {
            "transactionId": "tx-1",
            "holdId": "hold-1",
            "amount": "200000",
            "extraId": "extra-1",
            "cardId": "card-1",
        }
        tx = service._save_transaction(result, PlumTransactionType.HOLD, "HOLD_CONFIRMED")
        self.assertEqual(tx.transaction_id, "tx-1")
        self.assertEqual(tx.hold_id, "hold-1")
        self.assertEqual(tx.amount, Decimal("200000"))
        self.assertEqual(tx.status, "HOLD_CONFIRMED")
        tx2 = service._save_transaction(result, PlumTransactionType.HOLD, "HOLD_CONFIRMED")
        self.assertEqual(tx.id, tx2.id)
