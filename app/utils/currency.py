"""Display currency helpers.

VIP packages are priced and charged in VND (PayOS only settles VND), but the site
shows every amount to customers in USD. The conversion rate is a fixed, configurable
number — USD_VND_RATE, VND per 1 USD — so a displayed price never moves on its own.
"""
import os

try:
    USD_VND_RATE = float(os.getenv("USD_VND_RATE", "26000"))
    if USD_VND_RATE <= 0:
        raise ValueError
except ValueError:
    USD_VND_RATE = 26000.0


def vnd_to_usd(amount) -> float:
    """VND amount → USD, rounded to cents."""
    return round(float(amount or 0) / USD_VND_RATE, 2)
