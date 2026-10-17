import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    key = os.environ.get("JULES_API_KEY", "").strip()
    if not key:
        print(
            "JULES_API_KEY is missing. Add it in CircleCI Project Settings > "
            "Environment Variables."
        )
        return 2

    request = urllib.request.Request(
        "https://jules.googleapis.com/v1alpha/sources",
        headers={"x-goog-api-key": key, "Accept": "application/json"},
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        print(
            f"Jules API request failed with HTTP {exc.code}; "
            "check the key and Jules access."
        )
        return 3
    except Exception as exc:
        print(
            f"Could not reach Jules API ({type(exc).__name__}); "
            "check connectivity and retry."
        )
        return 4

    expected_owner = "BASSELEALGER"
    expected_repo = "digital-archive-V10-stable-backup"
    sources = payload.get("sources", [])
    matches = [
        item
        for item in sources
        if (item.get("githubRepo") or {}).get("owner", "").lower()
        == expected_owner.lower()
        and (item.get("githubRepo") or {}).get("repo", "").lower()
        == expected_repo.lower()
    ]

    if not matches:
        print(
            "API key works, but Jules does not list the approved repository "
            "as an available source."
        )
        print(
            "Open Jules > Settings/Integrations and connect this exact "
            "GitHub repository, then rerun."
        )
        return 5

    print("Jules API authentication succeeded.")
    print("Approved repository is available in Jules.")
    print("No coding session was created.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
