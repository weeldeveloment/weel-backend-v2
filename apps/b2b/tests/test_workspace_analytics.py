"""«Hisobotlar» — the redesigned report screen's endpoints.

Pinned here: how a period name and an anchor date become a window and its
comparison, which tabs a caller is offered, whose work is counted, how the
figures are shaped, and what the subscription endpoint refuses. The sums are
the database's and are not re-derived.
"""
from datetime import date, datetime, timezone as dt_tz
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="Asia/Tashkent", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace import analytics, analytics_io
from apps.b2b.workspace import analytics_repository as subs
from apps.b2b.workspace.analytics_tasks import period_for
from apps.b2b.workspace.analytics_views import (
    AnalyticsItemsView,
    AnalyticsReportView,
    AnalyticsSubscriptionView,
)
from apps.b2b.workspace.access import Module
from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.secondment import Membership

factory = APIRequestFactory()

OWNER = WorkspaceUser({"id": 1, "company_id": 55, "role": "owner", "full_name": "Egasi"})
MANAGER = WorkspaceUser({"id": 3, "company_id": 55, "role": "manager", "full_name": "Rahbar"})
EMPLOYEE = WorkspaceUser({"id": 7, "company_id": 55, "role": "employee", "full_name": "Xodim"})
# An employee whose workspace opened the reports module to the role — the
# role editor's doing, not the default.
READER = WorkspaceUser({
    "id": 8, "company_id": 55, "role": "employee", "full_name": "O'quvchi",
    "module_access": ["tasks", "chat", "sales", "reports"],
    "permission_access": ["tasks.view", "sales.view", "reports.view"],
})

# A Friday afternoon in Tashkent, five days into September.
NOW = datetime(2026, 9, 4, 9, 30, tzinfo=dt_tz.utc)  # 14:30 local


# ─── Windows ─────────────────────────────────────────────────────────────────

class TestWindows:
    def test_a_running_month_is_cut_at_now_and_compared_with_the_same_stretch(self):
        w = analytics.resolve_window("month", None, now=NOW)
        assert w.start_date == date(2026, 9, 1)
        assert w.end == NOW
        assert not w.complete
        # 1–4 September against 1–4 August, not against the whole of August.
        assert w.compare_start_date == date(2026, 8, 1)
        assert (w.compare_end - w.compare_start) == (w.end - w.start)

    def test_a_finished_month_is_compared_with_the_whole_previous_month(self):
        w = analytics.resolve_window("month", date(2026, 8, 10), now=NOW)
        assert w.start_date == date(2026, 8, 1)
        assert w.end_date == date(2026, 9, 1)
        assert w.complete
        assert w.compare_start_date == date(2026, 7, 1)
        assert w.compare_end_date == date(2026, 8, 1)

    def test_a_week_starts_on_monday_and_a_day_is_bucketed_by_the_hour(self):
        week = analytics.resolve_window("week", date(2026, 8, 12), now=NOW)  # a Wednesday
        assert week.start_date == date(2026, 8, 10)
        assert week.end_date == date(2026, 8, 17)
        assert week.bucket == "day" and len(week.buckets) == 7
        day = analytics.resolve_window("day", date(2026, 8, 12), now=NOW)
        assert day.bucket == "hour" and len(day.buckets) == 24
        assert day.compare_start_date == date(2026, 8, 11)

    def test_a_year_is_bucketed_by_month(self):
        w = analytics.resolve_window("year", date(2026, 3, 3), now=NOW)
        assert w.start_date == date(2026, 1, 1)
        assert w.bucket == "month"
        # January to September, the running month included.
        assert len(w.buckets) == 9

    def test_an_unknown_period_falls_back_and_a_future_anchor_is_today(self):
        w = analytics.resolve_window("fortnight", date(2030, 1, 1), now=NOW)
        assert w.period == analytics.DEFAULT_PERIOD
        assert w.start_date == date(2026, 9, 1)

    def test_labels_are_worded_for_the_reader(self):
        w = analytics.resolve_window("month", date(2026, 8, 10), now=NOW)
        assert analytics_io.window_label(w, "uz") == "1–31 avgust 2026"
        assert analytics_io.compare_label(w, "uz") == "iyul"
        assert analytics_io.compare_label(w, "ru") == "июль"
        running = analytics.resolve_window("month", None, now=NOW)
        assert analytics_io.window_label(running, "uz") == "1–4 sentyabr 2026"
        assert analytics_io.compare_label(analytics.resolve_window("day", None, now=NOW), "uz") == "kecha"


# ─── Shaping ─────────────────────────────────────────────────────────────────

class TestMetricShape:
    def test_change_is_relative_and_absent_without_a_baseline(self):
        assert analytics._change(48.5, 43.3) == pytest.approx(0.1201, abs=1e-4)
        assert analytics._change(10.0, 0.0) is None
        assert analytics._change(None, 3.0) is None

    def test_an_average_of_nothing_is_null_not_zero(self):
        spec = analytics.SALES_METRICS[4]  # cycle, days
        out = analytics._metric(spec, {}, {}, [{}], None)
        assert out["value"] is None and out["previous"] is None and out["change"] is None

    def test_money_leaves_as_a_string_and_snapshots_carry_no_trend(self):
        revenue = analytics.SALES_METRICS[0]
        out = analytics._metric(revenue, {"revenue": 48500000}, {"revenue": 43300000},
                                [{"revenue": 1}, {"revenue": 2}], None)
        assert out["value"] == "48500000.00" and out["spark"] == [1.0, 2.0]
        open_tasks = analytics.TASKS_METRICS[6]
        snap = analytics._metric(open_tasks, {}, {}, [], {"open": 6})
        assert snap["snapshot"] and snap["value"] == 6 and snap["spark"] == []

    def test_a_profit_is_revenue_less_cost(self):
        profit = analytics.SALES_METRICS[7]
        assert profit.value({"revenue": 100, "cogs": 40}) == 60

    def test_the_text_summary_reads_like_the_card(self):
        assert analytics_io.compact_money(48500000, "uz") == "48,5 mln so'm"
        assert analytics_io.format_value("percent", 0.342, "uz") == "34,2%"
        assert analytics_io.format_value("clock", 552, "uz") == "09:12"
        assert analytics_io.format_change(-0.11, "uz") == "−11%"


# ─── The endpoint ────────────────────────────────────────────────────────────

def _report(user, query=""):
    request = factory.get(f"/analytics/{query}")
    force_authenticate(request, user=user)
    with patch("apps.b2b.workspace.analytics_views.analytics.section_report") as report:
        report.return_value = {"section": "sales", "metrics": [], "employees": {"rows": []}}
        response = AnalyticsReportView.as_view()(request)
    return response, report


class TestWhoGetsWhichTabs:
    def test_a_plain_employee_has_no_reports_by_default(self):
        # TZ v2: an employee works what they are given, and the reports module
        # is not among it unless the workspace's role editor says so.
        response, report = _report(EMPLOYEE)
        assert response.status_code == 403
        report.assert_not_called()

    def test_an_employee_granted_reports_reads_their_own_tabs(self):
        response, report = _report(READER, "?section=tasks")
        assert response.status_code == 200
        # Only the modules their grant opens — no trips, and stock wants the
        # stock permission on top of the sales board.
        assert response.data["sections"] == ["sales", "tasks", "attendance"]
        assert response.data["scope"] == "own"
        assert report.call_args.kwargs["employee_id"] == READER.id
        assert response.data["can_filter_employee"] is False
        assert response.data["can_export"] is False

    def test_a_manager_gets_every_tab_but_exports_only_by_grant(self):
        response, _ = _report(MANAGER)
        assert response.data["sections"] == ["sales", "tasks", "stock", "trips", "attendance"]
        # TZ v2: the manager reads the reports; taking the figures off the
        # phone stays with the owner and the administrator unless the role
        # editor widens it.
        assert response.data["can_export"] is False
        owner, _ = _report(OWNER)
        assert owner.data["can_export"] is True

    def test_a_manager_reads_the_company_and_may_narrow_to_one_person(self):
        with patch("apps.b2b.workspace.analytics_views.fetch_one",
                   return_value={"id": 7, "full_name": "Aziz Karimov"}):
            response, report = _report(OWNER, "?employee_id=7")
        assert response.data["scope"] == "own"
        assert response.data["employee"] == {"id": 7, "full_name": "Aziz Karimov"}
        assert report.call_args.kwargs["employee_id"] == 7
        response, report = _report(OWNER)
        assert response.data["scope"] == "company"
        assert report.call_args.kwargs["employee_id"] is None

    def test_a_guest_lent_the_sales_board_gets_no_task_tab(self):
        # A guest's grant lives on their guest row (`module_access`), which
        # is what the access layer resolves; the membership carries the same
        # list for the older module gate. The guest role's own defaults hold
        # `reports.view`, so opening the module is enough.
        guest = WorkspaceUser(
            {"id": 9, "company_id": 55, "role": "guest", "full_name": "Mehmon",
             "module_access": [Module.SALES, Module.REPORTS]},
            membership=Membership(
                employee_id=9, company_id=55, home_employee_id=90, role="guest",
                modules=(Module.SALES, Module.REPORTS), starts_at=None, ends_at=None, is_active=True,
            ),
        )
        response, _ = _report(guest)
        assert response.status_code == 200
        assert "tasks" not in response.data["sections"]
        assert "sales" in response.data["sections"]
        refused, _ = _report(guest, "?section=tasks")
        assert refused.status_code == 403

    def test_an_unknown_section_is_a_bad_request(self):
        response, _ = _report(OWNER, "?section=payroll")
        assert response.status_code == 400

    def test_the_default_section_is_the_first_offered(self):
        response, report = _report(OWNER)
        assert response.data["section"] == "sales"
        assert report.call_args.args[1] == "sales"


class TestItems:
    def test_an_unknown_metric_is_refused_before_any_query(self):
        request = factory.get("/analytics/items/?section=sales&metric=nonsense")
        force_authenticate(request, user=OWNER)
        response = AnalyticsItemsView.as_view()(request)
        assert response.status_code == 400

    def test_the_page_carries_the_selection_totals(self):
        request = factory.get("/analytics/items/?section=sales&metric=revenue&sort=amount&limit=2")
        force_authenticate(request, user=OWNER)
        with patch("apps.b2b.workspace.analytics.fetch_one", return_value={"n": 127, "amount": 48500000}), \
             patch("apps.b2b.workspace.analytics.fetch_all", return_value=[]):
            response = AnalyticsItemsView.as_view()(request)
        assert response.status_code == 200
        assert response.data["count"] == 127
        assert response.data["amount"] == "48500000.00"
        assert response.data["limit"] == 2


class TestSubscription:
    def _put(self, body):
        request = factory.put("/analytics/subscription/?section=sales", body, format="json")
        force_authenticate(request, user=MANAGER)
        with patch("apps.b2b.workspace.analytics_views.subs.upsert_subscription") as upsert, \
             patch("apps.b2b.workspace.analytics_views._mail_available", return_value=False):
            upsert.side_effect = lambda *a, **k: {"section": "sales", **k}
            response = AnalyticsSubscriptionView.as_view()(request)
        return response, upsert

    def test_a_bad_address_and_a_bad_cadence_are_named(self):
        response, upsert = self._put({
            "is_enabled": True, "frequency": "hourly",
            "recipients": ["aziz@company.uz", "not-an-address"], "channels": ["email"],
        })
        assert response.status_code == 400
        assert "frequency" in response.data and "recipients" in response.data
        upsert.assert_not_called()

    def test_a_mailed_report_needs_somebody_to_mail(self):
        response, _ = self._put({"is_enabled": True, "frequency": "weekly", "recipients": [], "channels": ["email"]})
        assert response.status_code == 400
        assert "recipients" in response.data

    def test_a_good_order_is_stored_lower_cased_and_deduplicated(self):
        response, upsert = self._put({
            "is_enabled": True, "frequency": "weekly",
            "recipients": ["Aziz@Company.uz", "aziz@company.uz "], "channels": ["chat", "email", "fax"],
        })
        assert response.status_code == 200
        kwargs = upsert.call_args.kwargs
        assert kwargs["recipients"] == ["aziz@company.uz"]
        assert kwargs["channels"] == ["chat", "email"]
        assert response.data["mail_available"] is False

    def test_switching_off_needs_nothing_else(self):
        response, upsert = self._put({"is_enabled": False})
        assert response.status_code == 200
        assert upsert.call_args.kwargs["is_enabled"] is False


class TestSchedule:
    def test_which_cadences_fire_on_a_date(self):
        assert subs.frequencies_due(date(2026, 9, 4)) == ["daily"]           # Friday
        assert subs.frequencies_due(date(2026, 9, 7)) == ["daily", "weekly"]  # Monday
        assert subs.frequencies_due(date(2026, 10, 1)) == ["daily", "monthly"]

    def test_a_delivery_describes_the_period_that_ended(self):
        assert period_for("daily", date(2026, 9, 4)) == ("day", date(2026, 9, 3))
        assert period_for("weekly", date(2026, 9, 7)) == ("week", date(2026, 8, 31))
        assert period_for("monthly", date(2026, 9, 1)) == ("month", date(2026, 8, 31))
