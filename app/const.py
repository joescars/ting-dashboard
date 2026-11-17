"""Whisker Labs Ting cloud constants.

Values come from the community ha-whisker-ting integration (MIT), which
reverse-engineered the official Ting app. They are not a public API and
can change without notice.
"""

import os

COGNITO_REGION = "us-east-1"
COGNITO_USER_POOL_ID = "us-east-1_trW4gH661"
COGNITO_CLIENT_ID = "4akjeqt9gtl8rgg1cksunipk9u"
COGNITO_IDP_URL = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"

API_BASE_URL = "https://api.wskr.io"
API_USERS_ENDPOINT = "/api/v1/Users/{user_id}"
API_NOTIFICATIONS_ENDPOINT = "/api/v1/Notifications/history/{user_id}"
SIGNALR_URL = "wss://signalr.api.wskr.io/dataHub"

REQUEST_TIMEOUT = 30


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        return default
    return max(lo, min(hi, value))


DATA_DIR = os.environ.get("TING_DATA_DIR", "/data")
PORT = _int_env("PORT", 8097, 1, 65535)
POLL_INTERVAL = _int_env("TING_POLL_INTERVAL", 60, 30, 3600)
NOTIFICATION_INTERVAL = _int_env("TING_NOTIFICATION_INTERVAL", 300, 60, 3600)
BUCKET_SECONDS = _int_env("TING_BUCKET_SECONDS", 5, 1, 300)
RETENTION_DAYS = _int_env("TING_RETENTION_DAYS", 90, 1, 3650)
