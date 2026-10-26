"""Verify worker selection and round stop/resume without writing real data."""
import json
import os
from datetime import datetime, timedelta, timezone

from playwright.sync_api import expect, sync_playwright


def run():
    now = datetime.now(timezone.utc)
    r = {"id": 1, "level": 0, "status": "running", "paused": False, "worker_id": None,
         "opens_at": (now-timedelta(hours=2)).isoformat(), "closes_at": (now-timedelta(hours=1)).isoformat()}
    state = {"id": 1, "name": "Controls fixture", "status": "groups", "max_group_size": 4, "qualifiers": 2,
             "registration_open": now.isoformat(), "registration_close": now.isoformat(),
             "appeal_contact": "", "incident_policy": "", "auto_run": True, "auto_submission_minutes": 15,
             "roster": [], "rounds": [r], "holds": [], "notices": [], "allocation": None,
             "bracket": None, "encounters": []}
    workers = [{"id": "chosen", "online": True, "round_control_version": 1},
               {"id": "legacy", "online": True, "round_control_version": 0},
               {"id": "offline", "online": False, "round_control_version": 1}]
    writes, errors = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.add_init_script('sessionStorage.setItem("ottv2_admin_session","fixture-token")')
        page.on("pageerror", lambda error: errors.append(str(error)))

        def respond(route):
            path = route.request.url.split("/api/", 1)[1]
            if route.request.method != "GET":
                body = route.request.post_data_json
                writes.append((path, body))
                if path.endswith("/pause"): r["paused"] = True
                elif path.endswith("/resume"): r["paused"] = False
                elif path.endswith("/worker"): r["worker_id"] = body["worker_id"]
                else: raise AssertionError(path)
                data = {"id": 1, "status": r["status"]}
            elif path == "tournaments": data = [{"id": 1, "name": state["name"], "status": state["status"]}]
            elif path == "tournaments/1": data = state
            elif path.endswith("/audit"): data = {"actions": [], "attempts": []}
            elif path == "admin/workers": data = workers
            elif path == "admin/daily": data = {"enabled": False}
            elif path == "admin/daily/draws": data = []
            elif path.startswith("admin/daily/matches?"): data = {"items": [], "has_more": False}
            else: raise AssertionError(path)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

        page.route("**/api/**", respond)
        page.goto(os.environ["TEST_BROWSER_URL"]+"/admin")
        page.get_by_role("tab", name="Tournament", exact=True).click()
        select = page.locator("#roundWorker")
        expect(select.locator('[value="chosen"]')).to_be_enabled()
        expect(select.locator('[value="legacy"]')).to_have_attribute("disabled", "")
        expect(select.locator('[value="legacy"]')).to_contain_text("Cần cập nhật")
        expect(select.locator('[value="offline"]')).to_have_attribute("disabled", "")
        expect(page.locator("#pauseRound")).to_be_enabled()
        expect(page.locator("#resumeRound")).to_be_hidden()
        page.locator("#pauseRound").click()
        expect(page.locator("#resumeRound")).to_be_visible()
        expect(page.locator("#pauseRound")).to_be_hidden()
        expect(page.locator("#roundExecutionState")).to_contain_text("Vòng đã dừng")
        expect(page.locator('[data-round-action="finalize"]')).to_be_disabled()
        expect(page.locator("#forceControls")).to_be_hidden()
        select.select_option("chosen")
        page.locator("#saveRoundWorker").click()
        expect(select).to_have_value("chosen")
        page.locator("#resumeRound").click()
        expect(page.locator("#pauseRound")).to_be_visible()
        expect(page.locator("#roundExecutionState")).to_contain_text("Worker đã chọn: chosen")
        assert [path.rsplit("/", 1)[-1] for path, _ in writes] == ["pause", "worker", "resume"]
        assert writes[1][1] == {"worker_id": "chosen"}
        workers[0]["online"] = False
        page.evaluate("refresh()")
        expect(select).to_have_value("chosen")
        expect(page.locator("#roundExecutionState")).to_contain_text("không tự chuyển")
        for width in (1440, 375):
            page.set_viewport_size({"width": width, "height": 1000})
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        r["status"] = "finalized"
        page.evaluate("refresh()")
        expect(select).to_be_disabled()
        expect(page.locator("#pauseRound")).to_be_disabled()
        expect(page.locator("#saveRoundWorker")).to_be_disabled()
        assert not errors, errors
        browser.close()
    print("PASS: worker eligibility, stop/select/resume, offline pin, completed guards and mobile; APIs mocked")


if __name__ == "__main__":
    run()
