import datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
import urllib.parse
import uuid

import pytz
import requests
from flask import redirect, request, session


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get("user_id") is None:
            return redirect("/login")
        return f(*args, **kwargs)

    return decorated_function


def parse_positive_int(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None

    return number if number > 0 else None


def parse_positive_decimal(value):
    try:
        number = Decimal(value)
    except (TypeError, InvalidOperation):
        return None

    return number if number.is_finite() and number > 0 else None


def lookup(symbol):
    # Prepare API request
    symbol = symbol.upper()
    end = datetime.datetime.now(pytz.timezone("US/Eastern"))
    start = end - datetime.timedelta(days=7)

    # Yahoo Finance API with JSON response
    url = (
        "https://query2.finance.yahoo.com/v8/finance/chart/"
        f"{urllib.parse.quote_plus(symbol)}"
        f"?period1={int(start.timestamp())}"
        f"&period2={int(end.timestamp())}"
        f"&interval=1d&events=history"
    )

    # Query API
    try:
        response = requests.get(
            url,
            cookies={"session": str(uuid.uuid4())},
            headers={
                "Accept": "*/*",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/91.0.4472.124 Safari/537.36"
                ),
            },
            timeout=5,
        )
        response.raise_for_status()

        # Parse JSON response
        data = response.json()

        # Extract the most recent price from the JSON data
        if "chart" in data and "result" in data["chart"]:
            result = data["chart"]["result"][0]
            if "meta" in result:
                price = round(result["meta"]["regularMarketPrice"], 2)
                return {"price": price, "symbol": symbol}

    except (KeyError, IndexError, TypeError, requests.RequestException, ValueError):
        return None


def usd(value):
    return f"${value:,.2f}"
