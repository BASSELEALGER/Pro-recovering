import json
import os
import sys
import urllib.error
import urllib.request

API_BASE = "https://jules.googleapis.com/v1alpha"
APPROVED_BRANCH = "work/digital-archive-pro-verified-base-2026-10-12"
EXPECTED_OWNER = "BASSELEALGER"
EXPECTED_REPO = "digital-archive-V10-stable-backup"


def api_request(url: str, key: str, method: str = "GET", body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {
        "x-goog-api-key": key,
        "Accept": "application/json",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=45) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")) if raw else {}


def main() -> int:
    key = os.environ.get("JULES_API_KEY", "").strip()
    branch = os.environ.get("CIRCLE_BRANCH", "")
    if not key:
        print("JULES_API_KEY is missing from the CircleCI environment.")
        return 2
    if branch != APPROVED_BRANCH:
        print("Refusing to create a Jules session outside the approved work branch.")
        return 6

    try:
        payload = api_request(f"{API_BASE}/sources", key)
        sources = payload.get("sources", [])
        match = next(
            (
                item for item in sources
                if (item.get("githubRepo") or {}).get("owner", "").lower() == EXPECTED_OWNER.lower()
                and (item.get("githubRepo") or {}).get("repo", "").lower() == EXPECTED_REPO.lower()
            ),
            None,
        )
        if not match:
            print("The approved repository is not available as a Jules source.")
            return 5

        source_name = match.get("name")
        if not source_name:
            print("Jules returned the repository source without a resource name.")
            return 7

        prompt = (
            "Perform a READ-ONLY baseline audit of this Digital Archive Pro repository on the supplied starting branch. "
            "Do not edit, create, rename, or delete files. Do not commit, push, or open a pull request. "
            "Inspect the existing application structure, documented requirements, tests, packaging/build setup, and risks. "
            "Return a concise evidence-based report: (1) detected architecture and entry points, "
            "(2) existing tests and what they cover, (3) likely build/runtime compatibility risks, "
            "(4) discrepancies or missing requirements supported by specific file paths, "
            "(5) a prioritized proposed implementation plan with validation steps. "
            "Preserve the existing stable behavior and the required copyright mark © Noureddine 2026. "
            "Do not assume that a test passed unless you actually ran it and observed success."
        )
        session = api_request(
            f"{API_BASE}/sessions",
            key,
            method="POST",
            body={
                "prompt": prompt,
                "title": "Digital Archive Pro — Read-only baseline audit",
                "sourceContext": {
                    "source": source_name,
                    "githubRepoContext": {"startingBranch": APPROVED_BRANCH},
                },
                "requirePlanApproval": True,
            },
        )
    except urllib.error.HTTPError as exc:
        print(f"Jules API request failed with HTTP {exc.code}; no API key was printed.")
        return 3
    except Exception as exc:
        print(f"Could not create the Jules audit session ({type(exc).__name__}).")
        return 4

    session_url = session.get("url")
    session_id = session.get("id")
    if not session_url or not session_id:
        print("Jules returned a response without the expected session URL or ID.")
        return 8

    print("Jules read-only audit session created.")
    print(f"Session ID: {session_id}")
    print(f"Session URL: {session_url}")
    print(f"State: {session.get('state', 'unknown')}")
    print("Plan approval is required; no repository changes were authorized.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
