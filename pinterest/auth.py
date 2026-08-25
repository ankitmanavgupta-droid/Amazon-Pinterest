from urllib.parse import urlencode

import requests

from config import PINTEREST_API_BASE_URL, PINTEREST_APP_ID, PINTEREST_APP_SECRET, PINTEREST_REDIRECT_URI

# https://developers.pinterest.com/docs/getting-started/set-up-authentication-and-authorization/#choose-scopes
SCOPES = ("boards:read", "boards:write", "pins:read", "pins:write")

AUTHORIZATION_BASE_URL = "https://www.pinterest.com/oauth/"


def build_authorization_url(state: str) -> str:
    params = {
        "client_id": PINTEREST_APP_ID,
        "redirect_uri": PINTEREST_REDIRECT_URI,
        "response_type": "code",
        "scope": ",".join(SCOPES),
        "state": state,
    }
    return f"{AUTHORIZATION_BASE_URL}?{urlencode(params)}"


def exchange_code_for_token(code: str) -> dict:
    """Trade an authorization code for an access token + refresh token."""
    response = requests.post(
        f"{PINTEREST_API_BASE_URL}/oauth/token",
        auth=(PINTEREST_APP_ID, PINTEREST_APP_SECRET),
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": PINTEREST_REDIRECT_URI,
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
