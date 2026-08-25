import requests

from config import PINTEREST_ACCESS_TOKEN, PINTEREST_API_BASE_URL


class PinterestClient:
    def __init__(self, access_token: str = None, base_url: str = None):
        self.access_token = access_token or PINTEREST_ACCESS_TOKEN
        self.base_url = base_url or PINTEREST_API_BASE_URL
        if not self.access_token:
            raise ValueError("Missing PINTEREST_ACCESS_TOKEN. Complete the OAuth flow first (see pinterest/auth.py).")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.access_token}"

    def get_account(self) -> dict:
        response = self.session.get(f"{self.base_url}/user_account", timeout=10)
        response.raise_for_status()
        return response.json()

    def get_boards(self) -> list:
        response = self.session.get(f"{self.base_url}/boards", timeout=10)
        response.raise_for_status()
        return response.json().get("items", [])

    def create_pin(self, board_id: str, title: str, description: str, link: str, image_url: str) -> dict:
        payload = {
            "board_id": board_id,
            "title": title,
            "description": description,
            "link": link,
            "media_source": {
                "source_type": "image_url",
                "url": image_url,
            },
        }
        response = self.session.post(f"{self.base_url}/pins", json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
