from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from rest_framework.exceptions import ValidationError

from payment.exchange_rate import (
    _extract_usd_to_uzs_rate as extract_rate_exchange_module,
    exchange_rate,
    round_amount,
    to_usd,
    to_uzs,
)
from payment.services import PlumAPIError, PlumAPIService
from payment.tasks import _extract_usd_to_uzs_rate as extract_rate_task_module
from payment.tasks import update_exchange_rate


class ExchangeRateExtractionTests(SimpleTestCase):
    def test_extracts_rate_from_open_er_api_payload(self):
        payload = {"result": "success", "rates": {"UZS": 12900.55}}
        self.assertEqual(extract_rate_exchange_module(payload), Decimal("12900.55"))

    def test_extracts_rate_from_conversion_rates_payload(self):
        payload = {"success": True, "conversion_rates": {"UZS": "12750.10"}}
        self.assertEqual(extract_rate_exchange_module(payload), Decimal("12750.10"))

    def test_extracts_rate_from_legacy_cbu_payload(self):
        payload = [{"Ccy": "USD", "Rate": "12892,04"}]
        self.assertEqual(extract_rate_exchange_module(payload), Decimal("12892.04"))

    def test_raises_for_invalid_payload(self):
        with self.assertRaises(ValueError):
            extract_rate_exchange_module({"result": "success", "rates": {"EUR": 1}})

    def test_task_extractor_matches_exchange_module_behavior(self):
        payload = {"result": "success", "rates": {"UZS": 13001}}
        self.assertEqual(extract_rate_task_module(payload), Decimal("13001"))


class ExchangeRateFlowTests(SimpleTestCase):
    @patch("payment.exchange_rate.cache.get", return_value=Decimal("12600"))
    def test_exchange_rate_returns_cached_value(self, _mock_cache_get):
        self.assertEqual(exchange_rate(), Decimal("12600"))

    @patch("payment.exchange_rate.cache.set")
    @patch("payment.exchange_rate.cache.get", return_value=None)
    @patch("payment.exchange_rate._fetch_live_rate", return_value=Decimal("12777.7"))
    def test_exchange_rate_fetches_and_caches_when_missing(
        self,
        _mock_fetch_live_rate,
        _mock_cache_get,
        mock_cache_set,
    ):
        self.assertEqual(exchange_rate(), Decimal("12777.7"))
        self.assertGreaterEqual(mock_cache_set.call_count, 2)

    @patch("payment.exchange_rate.cache.get", return_value=None)
    @patch("payment.exchange_rate._fetch_live_rate", side_effect=Exception("network"))
    def test_exchange_rate_raises_validation_error_on_fetch_failure(
        self,
        _mock_fetch_live_rate,
        _mock_cache_get,
    ):
        with self.assertRaises(ValidationError):
            exchange_rate()

    def test_round_amount_rounds_to_nearest_10000(self):
        self.assertEqual(round_amount(Decimal("124999")), Decimal("120000"))
        self.assertEqual(round_amount(Decimal("125000")), Decimal("130000"))

    @patch("payment.exchange_rate.exchange_rate", return_value=Decimal("12000"))
    def test_to_uzs_uses_exchange_rate_and_rounding(self, _mock_exchange_rate):
        self.assertEqual(to_uzs(Decimal("100")), Decimal("1200000"))

    @patch("payment.exchange_rate.exchange_rate", return_value=Decimal("12800"))
    def test_to_usd_rounds_half_up(self, _mock_exchange_rate):
        self.assertEqual(to_usd(Decimal("25500")), Decimal("2"))


class UpdateExchangeRateTaskTests(SimpleTestCase):
    @patch("payment.tasks.cache.set")
    @patch("payment.tasks.requests.get")
    def test_update_exchange_rate_fetches_and_sets_cache(self, mock_get, mock_cache_set):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"result": "success", "rates": {"UZS": 12888.1}}
        mock_get.return_value = response

        update_exchange_rate()

        mock_cache_set.assert_called_once()
        args = mock_cache_set.call_args.args
        self.assertEqual(args[0], "usd_to_uzs_rate")
        self.assertEqual(args[1], Decimal("12888.1"))


def _mock_response(*, status_code: int, json_data=None, text: str = ""):
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.text = text
    if isinstance(json_data, Exception):
        response.json.side_effect = json_data
    else:
        response.json.return_value = json_data
    return response


class PlumServiceTests(SimpleTestCase):
    def test_normalize_card_number_strips_non_digits(self):
        self.assertEqual(PlumAPIService._normalize_card_number("8600 12-34 5678"), "860012345678")

    def test_normalize_expire_date_converts_mmyy_to_yymm(self):
        self.assertEqual(PlumAPIService._normalize_expire_date("12/28"), "2812")

    def test_normalize_phone_for_plum_strips_plus(self):
        self.assertEqual(PlumAPIService._normalize_phone_for_plum("+998 90 123 45 67"), "998901234567")

    def test_handle_response_raises_plum_error_for_http_error(self):
        service = PlumAPIService(
            auth_token="x",
            api_get_all_user_cards="http://example/cards",
            api_add_user_card="http://example/add",
            api_verify_user_card="http://example/verify",
            api_remove_user_card="http://example/remove",
            api_create_hold="http://example/create-hold",
            api_charge_hold="http://example/charge-hold",
            api_dismiss_hold="http://example/dismiss-hold",
            api_confirm_hold="http://example/confirm-hold",
            api_resend_otp="http://example/resend",
        )
        response = _mock_response(
            status_code=400,
            json_data={"errorMessage": {"message": "Card not accepted"}},
        )

        with self.assertRaises(PlumAPIError) as exc:
            service._handle_response(response)
        self.assertIn("Card not accepted", exc.exception.message)

    def test_handle_response_raises_for_non_json_403(self):
        service = PlumAPIService(
            auth_token="x",
            api_get_all_user_cards="http://example/cards",
            api_add_user_card="http://example/add",
            api_verify_user_card="http://example/verify",
            api_remove_user_card="http://example/remove",
            api_create_hold="http://example/create-hold",
            api_charge_hold="http://example/charge-hold",
            api_dismiss_hold="http://example/dismiss-hold",
            api_confirm_hold="http://example/confirm-hold",
            api_resend_otp="http://example/resend",
        )
        response = _mock_response(
            status_code=403,
            json_data=ValueError("invalid json"),
            text="Forbidden",
        )

        with self.assertRaises(PlumAPIError) as exc:
            service._handle_response(response)
        self.assertEqual(exc.exception.status_code, 403)

