import calendar
from datetime import datetime


def get_last_date_of_month(date_str: str) -> str:
    """
    Converts 'Dec 2025' or 'December 2025' to '31-12-2025'.
    """
    try:
        try:
            date_obj = datetime.strptime(date_str.strip(), "%b %Y")
        except ValueError:
            date_obj = datetime.strptime(date_str.strip(), "%B %Y")

        month = date_obj.month
        year = date_obj.year
        last_day = calendar.monthrange(year, month)[1]
        return f"{last_day:02d}-{month:02d}-{year}"
    except Exception:
        return date_str
