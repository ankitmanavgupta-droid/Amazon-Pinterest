from pinterest.client import PinterestClient


def main():
    client = PinterestClient()

    account = client.get_account()
    print(f"Connected as: {account.get('username')} ({account.get('account_type')})")

    boards = client.get_boards()
    print(f"\nBoards ({len(boards)}):")
    for board in boards:
        print(f"  - {board['name']} ({board['id']})")


if __name__ == "__main__":
    main()
