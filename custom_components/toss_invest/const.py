from datetime import timedelta

DOMAIN = "toss_invest"
PLATFORMS = ["sensor", "binary_sensor", "button", "switch", "event"]
DEFAULT_OPEN_PRICE_INTERVAL = timedelta(seconds=30)
DEFAULT_HOLDINGS_INTERVAL = timedelta(minutes=5)
DEFAULT_CLOSED_PRICE_INTERVAL = timedelta(minutes=10)
DEFAULT_REFERENCE_INTERVAL = timedelta(minutes=30)
