import secrets

from pinterest.auth import build_authorization_url, exchange_code_for_token


def main():
    state = secrets.token_urlsafe(16)
    print("Open this URL, approve access, then copy the `code` param from the redirect URL:\n")
    print(build_authorization_url(state))

    code = input("\nPaste the code here: ").strip()
    token_data = exchange_code_for_token(code)

    print("\nSuccess. Add this to your .env:\n")
    print(f"PINTEREST_ACCESS_TOKEN={token_data['access_token']}")
    if "refresh_token" in token_data:
        print(f"PINTEREST_REFRESH_TOKEN={token_data['refresh_token']}")


if __name__ == "__main__":
    main()
