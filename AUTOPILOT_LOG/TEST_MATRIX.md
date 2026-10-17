# Regression Test Matrix

Status values: PASS / FAIL / BLOCKED / NOT RUN only. A status describes an executed check, not an assumption.

| Test ID | Requirement / scenario | Exact command or procedure | Environment | Status | Evidence / outcome | Limitation / next action |
|---|---|---|---|---|---|---|
| BASE-001 | Approved branch and HEAD | GitHub REST GET `/repos/BASSELEALGER/digital-archive-V10-stable-backup/branches/work/digital-archive-pro-verified-base-2026-10-12` | GitHub API | PASS | HEAD `9bd11f71fc535e473770d72adf3ef75deee659d2` | Recheck before each write/commit. |
| BASE-002 | Required project documents readable | Fetch the four named Markdown files from approved branch | GitHub connector | PASS | All four fetched; latest recovery scope makes PDF Linking required despite older conflicting tracker sentence | Review complete contents as implementation proceeds. |
| PDF-001 | Multi-file multipart parser | `python -m py_compile app.py`; QA multipart route tests | Not run locally | NOT RUN | No execution in this session | Implement and run where execution environment exists. |
| PDF-002 | Filename-only matching across years/units | Isolated HTTP integration tests | Not run locally | NOT RUN | No execution in this session | Must not parse PDF contents; ambiguous matches must remain unlinked. |
| PDF-003 | Confirmation, rejection, safe unlink | Isolated HTTP integration tests; assert DB association removed while file and record remain | Not run locally | NOT RUN | No execution in this session | Must inspect attachment schema and current deletion behavior first. |
| SUBJ-001 | Empty incoming/outgoing subject | Existing QA and server persistence checks | Not run in this session | NOT RUN | Test source includes empty incoming subject case | Run full QA and verify outgoing/UI/server path too. |
| TPL-001 | Template categories, replacement, backup, isolation | Existing `tests/qa_smoke.py` cases | Not run in this session | NOT RUN | Test source includes upload, duplicate protection, replacement backup, unrelated-template preservation | Run and inspect actual implementation. |
| STAT-001 | Statistics agree with database records | Isolated fixture + HTTP checks | Not run in this session | NOT RUN | No fresh execution evidence | Verify each displayed count/filter. |
| RESET-001 | Reset safeguards and preservation | Reset only against isolated temporary test data | Not run in this session | NOT RUN | No fresh execution evidence | Never run against real user archive. |
| START-001 | Readiness, Chrome fallback, duplicate instance | Focused startup tests + supported OS checks | Not run in this session | NOT RUN | Current source contains helpers; this session has not run them | Do not infer Windows behavior from non-Windows testing. |
| REG-001 | Existing incoming/outgoing + traditional PDF upload | `python tests/qa_smoke.py` | Not run in this session | NOT RUN | No fresh execution evidence | Run after each coherent change. |
| BUILD-001 | Windows portable packaging/artifact provenance | Inspect CI run/jobs/artifacts; verify artifact commit | GitHub/CircleCI status not yet queried | NOT RUN | No current evidence collected in this step | Do not claim artifact or Windows 7 compatibility without evidence. |


| PDF-004 | PDF linker UI and folder scan route | Isolated HTTP integration cases added to `tests/qa_smoke.py` | Not executed in this environment | NOT RUN | Test code committed in `43359a7e1a8474814910b7bb3e159f611ad4a36d` | Must execute full QA; source change alone is not test evidence. |
| PDF-005 | `10.pdf` and `10.1.pdf` filename matching | Isolated HTTP integration case added | Not executed in this environment | NOT RUN | Test code asserts suffix attachment match | Must run and review actual results. |
| PDF-006 | Explicit confirm creates DB links; safe unlink preserves files | Isolated HTTP integration case added | Not executed in this environment | NOT RUN | Test code asserts association removal and file preservation | Must run; inspect failures and add ambiguous/duplicate-path cases if needed. |
| QA-001 | Multipart helper sends standards-shaped CRLF delimiters | Source inspected after patch | GitHub source inspection only | NOT RUN | Test source now contains Python `\\r\\n` escape sequences rather than double-escaped literal text | Must execute test suite; do not mark PASS from source inspection. |
| BUILD-001 | Portable Windows x86/x64 contains latest source | No build performed | Not executed | NOT RUN | Existing EXEs predate current source commits | Build on an available supported runner; no release until artifact provenance is verified. |


## Additional cases added 2026-10-13 — execution pending
- Smart restore preview is read-only; confirmation imports a numeric main record and 15.1-style attachment without inventing date/subject; repeat import skips duplicate records/links.
- Automatic filename-only linker creates unique links once and skips already linked files on a second pass.
- Reset endpoints reject incorrect administrator password; full reset retains a valid ZIP of the active archive outside the reset path.
- Dashboard reports PDF-linked records, unlinked records, and missing/broken attachment paths.
- PDF auto-linking setting is visible on the Settings page and can be enabled/disabled.

**Execution state for every item above: NOT RUN.** Do not mark PASS until python -m py_compile app.py and python tests/qa_smoke.py are executed and their output is recorded.
