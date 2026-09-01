"""«Hisobot va analitika» — one screen, one call.

What is pinned here is not arithmetic (the sums are the database's) but the
three decisions the endpoint makes on the reader's behalf: which window, whose
work, and which of the three sections they may see at all.
"""
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.models import LeadStage
from apps.b2b.workspace import repository as repo
from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.secondment import Membership, Module
from apps.b2b.workspace.views import WorkspaceReportView

factory = APIRequestFactory()

OWNER = WorkspaceUser({
    "id": 1,
    "company_id": 55,
    "role": "owner",
    "full_name": "Egasi",
})

EMPLOYEE = WorkspaceUser({
    "id": 7,
    "company_id": 55,
    "role": "employee",
    "full_name": "Xodim",
})


def _get(user, query=""):
    request = factory.get(f"/reports/{query}")
    force_authenticate(request, user=user)
    with patch("apps.b2b.workspace.views.repo", wraps=repo) as mocked:
        mocked.sales_report.return_value = {"won_count": 3}
        mocked.task_report.return_value = {"completed_count": 9}
        mocked.calendar_report.return_value = {"total_count": 4}
        response = WorkspaceReportView.as_view()(request)
    return response, mocked


class TestTheWindow:
    @pytest.mark.parametrize("period", ["week", "month", "quarter", "year"])
    def test_every_offered_period_is_answered(self, period):
        response, _ = _get(OWNER, f"?period={period}")
        assert response.status_code == 200
        assert response.data["period"]["period"] == period

    def test_an_unknown_period_falls_back_rather_than_failing(self):
        # The value comes off a query string and the screen is a page to read,
        # not a form to fail: a client sending a period this build does not
        # know still gets a report.
        response, _ = _get(OWNER, "?period=fortnight")
        assert response.status_code == 200
        assert response.data["period"]["period"] == repo.DEFAULT_REPORT_PERIOD

    def test_all_three_sections_are_counted_over_the_same_window(self):
        # Each section taking its own NOW() would have the three of them
        # disagreeing about where the month ended.
        _, mocked = _get(OWNER)
        windows = {
            tuple(call.kwargs[key] for key in ("start", "end", "bucket"))
            for call in (
                mocked.sales_report.call_args,
                mocked.task_report.call_args,
                mocked.calendar_report.call_args,
            )
        }
        assert len(windows) == 1

    def test_a_long_window_is_bucketed_wider_than_a_short_one(self):
        # 365 daily points on a phone-width chart is not a chart.
        assert repo.report_window("week")["bucket"] == "1 day"
        assert repo.report_window("year")["bucket"] == "1 month"


class TestWhoseNumbers:
    def test_a_manager_reads_the_company(self):
        response, mocked = _get(OWNER)
        assert response.data["scope"] == "company"
        assert mocked.sales_report.call_args.kwargs["employee_id"] is None

    def test_everybody_else_reads_their_own_work(self):
        # A salesperson's report is about their month. A company total on it
        # would be a number they cannot act on.
        response, mocked = _get(EMPLOYEE)
        assert response.data["scope"] == "own"
        for call in (
            mocked.sales_report.call_args,
            mocked.task_report.call_args,
            mocked.calendar_report.call_args,
        ):
            assert call.kwargs["employee_id"] == EMPLOYEE.id


class TestWhatEachSectionCovers:
    def test_a_permanent_employee_sees_all_three(self):
        response, _ = _get(EMPLOYEE)
        assert response.data["sales"] == {"won_count": 3}
        assert response.data["tasks"] == {"completed_count": 9}
        assert response.data["calendar"] == {"total_count": 4}

    def test_a_guest_sees_only_what_their_secondment_named(self):
        # Hiding a tab is not access control: the report spans three modules
        # and each one is gated on its own, so a guest lent the sales board
        # cannot read the task board through this endpoint.
        guest = WorkspaceUser(
            {"id": 9, "company_id": 55, "role": "employee", "full_name": "Mehmon"},
            membership=Membership(
                employee_id=9,
                company_id=55,
                home_employee_id=90,
                role="employee",
                modules=(Module.SALES,),
                starts_at=None,
                ends_at=None,
                is_active=True,
            ),
        )
        response, _ = _get(guest)
        assert response.data["sales"] is not None
        assert response.data["tasks"] is None
        assert response.data["calendar"] is None


class TestTheFunnelQuery:
    def test_open_pipeline_excludes_the_closed_stages(self):
        # "Open" is what is still out there to win. A won deal is money, not
        # pipeline, and a lost one is neither.
        with patch("apps.b2b.workspace.repository.fetch_one") as fetch_one, \
             patch("apps.b2b.workspace.repository.fetch_all") as fetch_all:
            fetch_one.return_value = {}
            fetch_all.return_value = []
            report = repo.sales_report(
                55,
                start=repo.timezone.now(),
                end=repo.timezone.now(),
                bucket="1 day",
            )

        sql = fetch_one.call_args.args[0]
        params = fetch_one.call_args.args[1]
        assert "AS open_count" in sql
        assert LeadStage.WON in params and LeadStage.LOST in params
        assert report["conversion_rate"] == 0.0

    def test_conversion_is_out_of_the_deals_that_were_decided(self):
        # Not out of every lead created: one still being worked has not failed
        # to convert, and counting it as a miss makes every healthy pipeline
        # look like a bad month.
        with patch("apps.b2b.workspace.repository.fetch_one") as fetch_one, \
             patch("apps.b2b.workspace.repository.fetch_all") as fetch_all:
            fetch_one.return_value = {
                "created_count": 100,
                "won_count": 3,
                "lost_count": 1,
                "won_amount": 300,
                "open_count": 96,
                "open_amount": 9600,
            }
            fetch_all.return_value = []
            report = repo.sales_report(
                55,
                start=repo.timezone.now(),
                end=repo.timezone.now(),
                bucket="1 day",
            )

        assert report["conversion_rate"] == 0.75
        assert report["average_deal"] == "100"
        # Money leaves as a string: NUMERIC(14, 2) in so'm does not survive a
        # JSON float intact.
        assert isinstance(report["won_amount"], str)

    def test_the_funnel_lists_every_open_stage_even_an_empty_one(self):
        with patch("apps.b2b.workspace.repository.fetch_one") as fetch_one, \
             patch("apps.b2b.workspace.repository.fetch_all") as fetch_all:
            fetch_one.return_value = {}
            # by_stage, then by_source, lost_reasons, trend and the
            # leaderboard — only the first has rows here.
            fetch_all.side_effect = [
                [{"stage": LeadStage.PROPOSAL, "count": 2, "amount": 500}],
                [], [], [], [],
            ]
            report = repo.sales_report(
                55,
                start=repo.timezone.now(),
                end=repo.timezone.now(),
                bucket="1 day",
            )

        stages = [row["stage"] for row in report["by_stage"]]
        # In funnel order, and with the two closed stages left out — a funnel
        # with a step missing reads as a bug in the funnel.
        assert stages == [
            stage for stage in LeadStage.ORDER if stage not in LeadStage.CLOSED
        ]
        assert report["by_stage"][stages.index(LeadStage.PROPOSAL)]["count"] == 2


class TestTheBoardQuery:
    def test_on_time_is_out_of_the_tasks_that_had_a_deadline(self):
        # A task with no due date was never late by definition, and counting
        # it either way only dilutes the rate.
        with patch("apps.b2b.workspace.repository.fetch_one") as fetch_one, \
             patch("apps.b2b.workspace.repository.fetch_all") as fetch_all:
            fetch_one.return_value = {
                "created_count": 20,
                "completed_count": 10,
                "due_count": 4,
                "on_time_count": 3,
                "open_count": 10,
                "overdue_count": 2,
                "due_today_count": 1,
                "todo_count": 6,
                "in_progress_count": 4,
            }
            fetch_all.return_value = []
            report = repo.task_report(
                55,
                start=repo.timezone.now(),
                end=repo.timezone.now(),
                bucket="1 day",
            )

        assert report["on_time_rate"] == 0.75
        assert [row["priority"] for row in report["by_priority"]] == list(
            repo.TASK_PRIORITIES
        )


class TestTheCalendarQuery:
    def test_all_day_entries_are_counted_but_book_no_hours(self):
        # A day blocked out for a trip is not eight hours of meetings, and
        # letting it claim twenty-four would swamp every real figure beside it.
        with patch("apps.b2b.workspace.repository.fetch_one") as fetch_one, \
             patch("apps.b2b.workspace.repository.fetch_all") as fetch_all:
            fetch_one.return_value = {
                "total_count": 6,
                "all_day_count": 2,
                "hours": 7.5,
                "count": 3,
            }
            fetch_all.return_value = []
            report = repo.calendar_report(
                55,
                start=repo.timezone.now(),
                end=repo.timezone.now(),
                bucket="1 day",
            )

        sql = fetch_one.call_args_list[0].args[0]
        assert "FILTER (WHERE NOT e.all_day)" in sql
        assert report["total_count"] == 6
        assert report["hours"] == 7.5
        # Every weekday appears, so an empty Sunday is a gap in the bar chart
        # rather than a missing bar.
        assert [row["weekday"] for row in report["by_weekday"]] == list(range(1, 8))
