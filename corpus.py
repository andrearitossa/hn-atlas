"""Calendar window shared by search corpus synchronization."""
import calendar
from datetime import datetime, timezone


def cutoff(as_of):
    date = datetime.fromtimestamp(as_of, timezone.utc)
    month_index = date.year * 12 + date.month - 1 - 3
    year, month = divmod(month_index, 12)
    month += 1
    return int(date.replace(year=year, month=month, day=min(date.day, calendar.monthrange(year, month)[1])).timestamp())

