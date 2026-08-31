# Gradescope rubric flow discovery

## Objective and current status

Goal: discover and validate the Playwright interaction sequence for rubric work:
login → course → assignment → rubric editor → upload/import → save/publish.

Current status: discovery tooling is implemented in `scripts/discover_rubric_flow.py`.
This is **not** complete end-to-end rubric upload automation yet. It is intended to
collect repeatable diagnostics (trace/screenshots/step logs) while selectors are
iteratively validated.

## Required environment/configuration

- Run from the repository root directory (`.`)
- Use existing secure credential approach already used by `GradescopeClient`:
  - optional `.env` keys: `GRADESCOPE_USERNAME`, `GRADESCOPE_PASSWORD`
  - existing storage state file from `edubag gradescope client authenticate`
- Do **not** hardcode credentials in code, scripts, or docs.

Example run:

```bash
uv run python scripts/discover_rubric_flow.py \
  --course "MATH-UA 122.006 Calculus II, Spring 2026" \
  --assignment "Quiz 1" \
  --term "Spring 2026" \
  --rubric-file /absolute/path/to/rubric.csv \
  --artifacts-dir /absolute/path/to/artifacts \
  --headed
```

## Expected UI milestone sequence

1. `login_complete`
2. `course_opened`
3. `assignment_opened`
4. `rubric_editor_reached`
5. `upload_attempted`
6. `save_attempted`

Each milestone writes structured step logs and checkpoint screenshots.

## Artifacts produced

Under `--artifacts-dir`:

- `trace.zip` (Playwright trace)
- `step_logs.json` (structured step diagnostics)
- `login_complete.png`
- `course_opened.png`
- `assignment_opened.png`
- `rubric_editor_reached.png`
- `upload_attempted.png`
- `save_attempted.png`

## Selector table (update as discoveries are validated)

| step | preferred locator | fallback locator | notes |
|---|---|---|---|
| login | `get_by_role("button", name="Log In")` | login URL direct navigation | may already be authenticated |
| course | `get_by_role("link", name=/<course>/i)` | `https://gradescope.com/courses/<id>` | `--term` is currently a hint in logs |
| assignment | `get_by_role("link", name=/<assignment>/i)` | `/courses/<course_id>/assignments/<id>` URL | assignment id path works when course id is known |
| rubric entry | `get_by_role("tab" \| "link" \| "button", name=/Rubric/i)` | UI-specific CSS fallback if needed | keep role/label/text first |
| rubric upload/import | `get_by_label(/Upload Rubric/i)` / `get_by_role("button", name=/Upload\|Import Rubric/i)` | `input[type='file']` | discovery script continues if no upload control is found |
| save/publish | `get_by_role("button", name=/Save Rubric\|Save\|Publish/i)` | none yet | `--attempt-save` required to click |

## Troubleshooting

- Authentication redirected to login:
  - refresh auth state with `edubag gradescope client authenticate`
  - confirm env vars are set if running headless login
- Course/assignment not found by text:
  - retry with numeric IDs or full URLs
  - review `step_logs.json` + `trace.zip` to refine selectors
- Upload control missing:
  - expected during discovery on some assignment types
  - script logs probe diagnostics and still writes artifacts
- Unexpected selector drift:
  - update locator candidates in discovery script and rerun

## Iterative status note

This document and the discovery script are living artifacts. Update selector rows
and milestone notes as UI behavior is confirmed.
