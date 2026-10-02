"""Which day a sale is counted on in the reports.

A sale counts on the day it was sold. The one exception is a sale that waited
for finalization across a month boundary: sold on the 28th, confirmed on the
3rd of the next month. Its own month never saw it confirmed, so it counts on
the day it was confirmed instead, and lands in the month that closed it.

A sale confirmed within the month it was sold stays on the day it was sold.

Both timestamps are stored in UTC; months are compared on the local clock,
which is the one the reports group by.
"""

from datetime import datetime, timezone

from sqlalchemy import and_, case, func

_STORED_FORMAT = "%Y-%m-%d %H:%M:%S"


def report_time_expr(created_at, finalized_at):
    """SQL for the moment a sale counts in the reports (UTC, like the columns)."""
    confirmed_in_a_later_month = and_(
        finalized_at.is_not(None),
        func.strftime("%Y-%m", finalized_at, "localtime")
        > func.strftime("%Y-%m", created_at, "localtime"),
    )
    return case((confirmed_in_a_later_month, finalized_at), else_=created_at)


def report_date_expr(created_at, finalized_at):
    """SQL for the local date a sale counts on, comparable to ``YYYY-MM-DD``."""
    return func.date(report_time_expr(created_at, finalized_at), "localtime")


def report_time(created_at, finalized_at):
    """Python twin of :func:`report_time_expr`, for rows already loaded."""
    if finalized_at and _local_month(finalized_at) > _local_month(created_at):
        return finalized_at
    return created_at


def _local_month(value):
    if not value:
        return ""
    try:
        moment = datetime.strptime(str(value), _STORED_FORMAT)
    except ValueError:
        return str(value)[:7]
    return moment.replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m")
