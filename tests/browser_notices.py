"""Opt-in notice UI check against an empty, disposable deployment.

Set TEST_BROWSER_URL, TEST_ADMIN_PASSWORD and PLAYWRIGHT_BROWSERS_PATH.
Never point this at a live tournament. No worker is needed for this check.
"""
import os
from datetime import datetime, timedelta, timezone

from playwright.sync_api import expect, sync_playwright


def run():
    url = os.environ["TEST_BROWSER_URL"]
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        admin = browser.new_page(viewport={"width": 1440, "height": 1000})
        player = browser.new_page(viewport={"width": 1440, "height": 1000})
        for page in (admin, player):
            page.on("pageerror", lambda error: errors.append(str(error)))
        response = admin.request.get(url + "/api/tournaments")
        assert response.ok and response.json() == [], "Use an empty disposable deployment"
        admin.goto(url + "/admin")
        admin.locator("#adminPassword").fill(os.environ["TEST_ADMIN_PASSWORD"])
        admin.locator("#adminLoginForm button").click()
        expect(admin.locator("#adminControls")).to_be_visible()
        expect(admin.locator("#publishNotice")).to_be_disabled()
        now = datetime.now(timezone.utc)
        local = timezone(timedelta(hours=7))
        form = admin.locator("#tournamentConfig")
        form.locator('[name="name"]').fill("Notice Browser Test Cup")
        form.locator('[name="registration_open"]').fill((now-timedelta(hours=1)).astimezone(local).strftime("%Y-%m-%dT%H:%M"))
        form.locator('[name="registration_close"]').fill((now+timedelta(hours=1)).astimezone(local).strftime("%Y-%m-%dT%H:%M"))
        form.locator('[name="appeal_contact"]').fill("appeals@example.test")
        form.locator('[name="incident_policy"]').fill("Postpone affected round after 24 hours")
        form.locator('button[type="submit"]').click()
        expect(admin.locator("#publishNotice")).to_be_enabled()
        expect(admin.locator("#noticeText")).to_have_value("")
        message = "<b>Literal notice</b>\nManual second line"
        admin.locator("#noticeKind").select_option("rules_update")
        admin.locator("#noticeText").fill(message)
        admin.locator("#publishNotice").click()
        expect(admin.locator("#adminNotices .notice-text")).to_have_text(message)
        expect(admin.locator("#noticeText")).to_have_value("")
        expect(admin.locator("#adminNotices b")).to_have_count(0)
        player.goto(url)
        expect(player.locator("#tournamentNotices")).to_contain_text(message)
        expect(player.locator("#tournamentNotices b")).to_have_count(0)
        admin.reload()
        expect(admin.locator("#adminNotices .notice-text")).to_have_text(message)
        admin.locator("#noticeText").fill("Unpublished draft")
        admin.evaluate("refresh()")
        expect(admin.locator("#noticeText")).to_have_value("Unpublished draft")
        admin.locator("#adminNotices [data-remove-notice]").click()
        expect(admin.locator("#adminNotices .admin-notice")).to_have_count(0)
        player.reload()
        expect(player.locator("#tournamentNotices")).to_have_count(0)
        for text in ("Manual notice one", "Manual notice two"):
            admin.locator("#noticeKind").select_option("notice")
            admin.locator("#noticeText").fill(text)
            admin.locator("#publishNotice").click()
            expect(admin.locator("#noticeText")).to_have_value("")
        expect(admin.locator("#adminNotices .admin-notice")).to_have_count(2)
        admin.locator("#clearNotices").click()
        expect(admin.locator("#adminNotices .admin-notice")).to_have_count(0)
        expect(admin.locator("#clearNotices")).to_be_disabled()
        token = admin.evaluate("adminToken")
        tid = admin.evaluate("tournamentId")
        audit = admin.request.get(f"{url}/api/admin/tournaments/{tid}/audit", headers={"Authorization": "Bearer " + token}).json()
        notices = [a for a in audit["actions"] if a["kind"] in ("notice", "rules_update")]
        assert len(notices) == 3 and all(a["notice_hidden_at"] for a in notices)
        assert not errors, errors
        browser.close()
    print("PASS: notice publish, literal text, persistence, draft retention, remove, clear, audit; no JavaScript errors")


if __name__ == "__main__":
    run()
