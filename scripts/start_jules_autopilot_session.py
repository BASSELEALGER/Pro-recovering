import json
import os
import sys
import urllib.error
import urllib.request

API_BASE = "https://jules.googleapis.com/v1alpha"
APPROVED_BRANCH = "work/digital-archive-pro-verified-base-2026-10-12"
EXPECTED_OWNER = "BASSELEALGER"
EXPECTED_REPO = "digital-archive-V10-stable-backup"

PROMPT = """Act as the implementation agent for the Digital Archive Pro release candidate. This is an EXECUTE-AND-VERIFY task, not a read-only audit. Read SESSION_RECOVERY.md, PROJECT_TRACKER.md, FINAL_AUDIT_REPORT.md, and PROMPT_SAHIQA_MAHIQA.md first, then inspect the actual source, tests, build workflows, and latest available CI evidence.

WORKING RULES:
- Work only on starting branch work/digital-archive-pro-verified-base-2026-10-12. Never modify main, switch to main, merge any branch, or open a pull request. Do not change branch protection or secrets.
- Preserve stable behavior and keep changes limited, testable, and reversible. Do not delete user data, databases, archives, attachments, or backups.
- Do not claim tests/builds passed unless you ran them and observed success. Distinguish code/test failures from CI infrastructure failures.
- Preserve the exact copyright mark © Noureddine 2026.
- Follow the newest recovery instructions where project documents conflict; record the conflict and rationale in the tracker rather than silently dropping a requirement.
- Do not ask the user routine questions. Choose conservative defaults; if a destructive or genuinely ambiguous decision is unavoidable, leave that part untouched and document the blocker.

TASK:
1. Establish the actual branch/commit baseline and inspect recent CI runs. Identify all outstanding release blockers from the repository documents and code.
2. Implement the remaining approved requirements in priority order, including Word template management and safe replacement, optional correspondence subject, safe reset/backup behavior, local startup readiness and duplicate-process prevention, statistics/navigation, and the standalone PDF Linking workflow as specified in SESSION_RECOVERY.md: match by PDF filename only (never read PDF contents), search across years and units, show a review table, require explicit user confirmation before final linking, and make unlinking preserve both the file and record. Keep the traditional incoming/outgoing creation and PDF upload flow unchanged.
3. Add or update focused regression tests for each change, including failure and data-preservation cases. Run the relevant tests and available packaging checks. Use CI workflows where available for Windows portable builds; do not claim Windows 7 SP1 32-bit compatibility without a real test on that OS.
4. Fix actual code failures where feasible. If CI infrastructure fails before any job step starts, record that as infrastructure-blocked and do not mislabel it as a test failure.
5. Update PROJECT_TRACKER.md, FINAL_AUDIT_REPORT.md, and SESSION_RECOVERY.md with precise completed/pending status, commands actually run, outcomes, commit SHAs, and links to real CI runs/artifacts if available. Do not invent artifact links.
6. Keep all work on the approved work branch and commit the coherent, tested changes there. Do not open a PR, merge, or touch main.

Deliver a concise final summary with changed files, commits, tests actually run and results, unresolved blockers, and any genuine artifact links. Continue implementation after analysis; do not stop after producing a plan unless the repository or API blocks execution.
"""

def api_request(url: str, key: str, method: str = "GET", body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"x-goog-api-key": key, "Accept": "application/json"}
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
        print("Refusing to start Jules implementation outside the approved work branch.")
        return 6
    try:
        payload = api_request(f"{API_BASE}/sources", key)
        sources = payload.get("sources", [])
        match = next((item for item in sources
            if (item.get("githubRepo") or {}).get("owner", "").lower() == EXPECTED_OWNER.lower()
            and (item.get("githubRepo") or {}).get("repo", "").lower() == EXPECTED_REPO.lower()), None)
        if not match or not match.get("name"):
            print("The approved repository is not available as a Jules source.")
            return 5
        session = api_request(
            f"{API_BASE}/sessions", key, method="POST",
            body={
                "prompt": PROMPT,
                "title": "Digital Archive Pro — autonomous implementation and verification",
                "sourceContext": {
                    "source": match["name"],
                    "githubRepoContext": {"startingBranch": APPROVED_BRANCH},
                },
                "requirePlanApproval": False,
            },
        )
    except urllib.error.HTTPError as exc:
        print(f"Jules API request failed with HTTP {exc.code}; no API key was printed.")
        return 3
    except Exception as exc:
        print(f"Could not create the Jules implementation session ({type(exc).__name__}).")
        return 4
    session_url = session.get("url")
    session_id = session.get("id")
    if not session_url or not session_id:
        print("Jules returned a response without the expected session URL or ID.")
        return 8
    print("Jules autonomous implementation session created.")
    print(f"Session ID: {session_id}")
    print(f"Session URL: {session_url}")
    print(f"State: {session.get('state', 'unknown')}")
    print("Plan approval is disabled for this session; implementation is authorized on the guarded work branch only.")
    print("No pull request is requested by this script; main must remain untouched.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
