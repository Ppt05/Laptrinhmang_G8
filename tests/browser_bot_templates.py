"""Verify starter code, API comments and draft retention. All API calls are mocked."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def run():
    now = datetime.now(timezone.utc)
    tournament = {"id": 1, "name": "Template fixture", "status": "groups", "max_group_size": 4,
                  "qualifiers": 2, "registration_open": now.isoformat(), "registration_close": now.isoformat(),
                  "appeal_contact": "fixture@example.test", "incident_policy": "Fixture only", "roster": [],
                  "rounds": [{"id": 1, "level": 0, "status": "submission",
                              "opens_at": (now - timedelta(hours=1)).isoformat(),
                              "closes_at": (now + timedelta(hours=1)).isoformat()}],
                  "groups": [], "encounters": [], "bracket": None, "draws": [], "notices": [],
                  "environment": {"bot_entrypoint": "functions-v1"}, "rules_digest": "fixture"}
    submissions, errors = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script('sessionStorage.setItem("ottv2_session", "fixture-token")')

        def respond(route):
            path = route.request.url.split("/api/", 1)[1].split("?",1)[0]
            if path == "submissions" and route.request.method == "POST":
                submissions.append(route.request.post_data_json)
                assert route.request.headers.get("idempotency-key")
                data = {"submission_id": len(submissions), "status": "queued"}
            elif path == "me":
                data = {"name": "Fixture team"}
            elif path == "tournaments":
                data = [{"id": 1, "name": tournament["name"], "status": tournament["status"]}]
            elif path == "tournaments/1":
                data = tournament
            elif path == "tournaments/1/team/status":
                data = {"enrolled": True, "enrollment_status": "enrolled", "attempts_used": len(submissions),
                        "can_submit":True,"submission_block_reason":"",
                        "selected_submission": None, "locked_submission": None,
                        "eligibility_reason": "awaiting_bot_lock", "elo": 1000}
            elif path in ("leaderboard", "submissions", "my/matches"):
                data = []
            else:
                raise AssertionError(path)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

        page.route("**/api/**", respond)
        page.goto(os.environ["TEST_BROWSER_URL"])
        source = page.locator('#submitForm [name="source"]')
        language = page.locator('#submitForm [name="language"]')
        submit = page.locator('#submitForm button[type="submit"]')
        expect(source).to_be_visible()
        expect(submit).to_be_enabled()
        python = source.input_value()
        assert python.startswith("# API:") and "def choose_move(state):" in python
        assert all(field in python for field in ("pieces", "legal_moves", "piece_id", "capture", "0..8"))
        python_draft = python + "\n# Python draft\n"
        source.fill(python_draft)
        language.select_option("cpp")
        cpp = source.input_value()
        assert cpp.startswith("// API:") and "Move chooseMove(const BoardState& state)" in cpp
        assert all(field in cpp for field in ("state.at(x,y)", "state.board[y][x]", "capture", "0..8"))
        cpp_draft = cpp + "\n// C++ draft\n"
        source.fill(cpp_draft)
        language.select_option("python")
        expect(source).to_have_value(python_draft)
        submit.click()
        expect(page.locator("#submitForm .form-message")).to_contain_text("#1")
        expect(source).to_have_value(python_draft)
        language.select_option("cpp")
        expect(source).to_have_value(cpp_draft)
        submit.click()
        expect(page.locator("#submitForm .form-message")).to_contain_text("#2")
        expect(source).to_have_value(cpp_draft)
        assert submissions == [{"language": "python", "source": python_draft},
                               {"language": "cpp", "source": cpp_draft}]
        page.locator('[data-open-rules]').first.click()
        expect(page.locator("#rulesDialog")).to_be_visible()
        expect(page.locator("#rulesDialog")).to_contain_text("state.at(x, y)")
        expect(page.locator("#rulesDialog .bot-template-code")).to_have_count(2)
        for width in (1440, 375):
            page.set_viewport_size({"width": width, "height": 1000})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), width
        page.locator("#closeRules").click()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors, errors
        if os.getenv("TEST_TEMPLATE_OUTPUT"):
            Path(os.environ["TEST_TEMPLATE_OUTPUT"]).write_text(json.dumps({"python": python, "cpp": cpp}))
        browser.close()
    print("PASS: API comments, both starter functions, independent drafts, submitted source, desktop/mobile rules; APIs mocked")


if __name__ == "__main__":
    run()
