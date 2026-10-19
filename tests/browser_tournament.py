"""Opt-in browser check against an empty, disposable deployment.

Run separately from unittest, with TEST_BROWSER_URL, TEST_DATABASE_URL,
TEST_ADMIN_PASSWORD and PLAYWRIGHT_BROWSERS_PATH. The normal worker must run
against that same disposable database. Never point this at a live tournament.
"""
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright
from sqlalchemy import create_engine, text


def input_time(value):
    return value.astimezone(timezone(timedelta(hours=7))).strftime("%Y-%m-%dT%H:%M")


def run():
    url = os.environ["TEST_BROWSER_URL"]
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        admin = browser.new_page(viewport={"width": 1440, "height": 1000})
        player = browser.new_page(viewport={"width": 1440, "height": 1000})
        for page in (admin, player):
            page.on("pageerror", lambda error: errors.append(str(error)))
        admin.goto(url + "/admin")
        admin.locator("#adminPassword").fill(os.environ["TEST_ADMIN_PASSWORD"])
        admin.locator("#adminLoginForm button").click()
        admin.locator("#adminControls").wait_for(state="visible")
        now = datetime.now(timezone.utc)
        form = admin.locator("#tournamentConfig")
        form.locator('[name="name"]').fill("Browser Test Cup")
        form.locator('[name="registration_open"]').fill(input_time(now-timedelta(hours=1)))
        form.locator('[name="registration_close"]').fill(input_time(now+timedelta(hours=1)))
        form.locator('[name="appeal_contact"]').fill("appeals@example.test")
        form.locator('[name="incident_policy"]').fill("Postpone affected round after 24 hours")
        form.locator('button[type="submit"]').click()
        admin.wait_for_function('tournament?.status === "registration"')
        tid = admin.evaluate("tournament.id")
        player.goto(url)
        player.locator("#navRegister").click()
        player.locator('#registerForm [name="name"]').fill("Browser Team 1")
        player.locator('#registerForm [name="password"]').fill("browser-team-password")
        player.locator('#registerForm button[type="submit"]').click()
        player.locator("#accountDialog").wait_for(state="hidden")
        player.locator("#joinTournament").click()
        player.wait_for_function('document.querySelector("#joinTournament").disabled')
        for i in range(2, 5):
            response = player.request.post(url+"/api/register", data={"name":f"Browser Team {i}","password":"browser-team-password"})
            assert response.ok, response.text()
            token = response.json()["token"]
            response = player.request.post(f"{url}/api/tournaments/{tid}/join",headers={"Authorization":"Bearer "+token})
            assert response.ok, response.text()
        admin.locator('[data-action="lock-roster"]').click()
        admin.wait_for_function('tournament?.status === "allocated"')
        admin.locator('[data-action="confirm-groups"]').click()
        admin.wait_for_function('tournament?.status === "groups"')
        admin.locator("#windowOpen").fill(input_time(now-timedelta(minutes=1)))
        admin.locator("#windowClose").fill(input_time(now+timedelta(minutes=5)))
        admin.locator('#windowForm button[type="submit"]').click()
        admin.wait_for_function('round()?.status === "submission"')
        rid = admin.evaluate("round().id")
        player.reload()
        player.locator('#submitForm textarea').fill(Path("examples/bot_greedy.py").read_text())
        player.locator('#submitForm button[type="submit"]').click()
        player.wait_for_function('document.querySelector("#submitForm .form-message").textContent.includes("Đã nhận")')
        for _ in range(60):
            response = player.request.get(url+"/api/submissions",headers={"Authorization":"Bearer "+player.evaluate('sessionStorage.getItem("ottv2_session")')})
            if response.json()[0]["status"] == "ready":
                break
            time.sleep(1)
        else:
            raise AssertionError("Worker did not validate submitted bot")
        # Seed other teams through the same API, using real worker compilation.
        for i in range(2, 5):
            token = player.request.post(url+"/api/login",data={"name":f"Browser Team {i}","password":"browser-team-password"}).json()["token"]
            response = player.request.post(f"{url}/api/tournaments/{tid}/rounds/{rid}/submissions",headers={"Authorization":"Bearer "+token,"Idempotency-Key":"browser-real-bot"},data={"language":"python","source":Path("examples/bot_greedy.py").read_text()})
            assert response.ok, response.text()
        for _ in range(60):
            with engine.connect() as conn:
                pending = conn.scalar(text("SELECT count(*) FROM submissions WHERE status <> 'ready'"))
            if not pending:
                break
            time.sleep(1)
        else:
            raise AssertionError("Other bots did not finish validation")
        # Test clock only: move the deadline back instead of waiting five minutes.
        with engine.begin() as conn:
            conn.execute(text("UPDATE tournament_rounds SET closes_at=now()-interval '1 second' WHERE id=:rid"),{"rid":rid})
        admin.locator('[data-round-action="lock-bots"]').click()
        admin.wait_for_function('round()?.status === "locked"')
        admin.locator('[data-round-action="start-batch"]').click()
        admin.wait_for_function('round()?.status === "running"')
        for _ in range(90):
            data = player.request.get(f"{url}/api/tournaments/{tid}").json()
            first = [e for e in data["encounters"] if e["batch"] == 1]
            if all(e["status"] == "provisional" for e in first):
                break
            time.sleep(1)
        else:
            raise AssertionError("Batch did not complete")
        player.reload()
        player.locator("#tournamentEncounters details").first.locator("summary").click()
        player.locator("#tournamentEncounters [data-replay-id]").first.click()
        player.locator("#replayDialog").wait_for(state="visible")
        player.wait_for_function('document.querySelectorAll("#replayBoard .replay-cell").length === 81')
        admin.locator('[data-round-action="finalize-encounters"]').click()
        admin.wait_for_function('document.querySelector("#adminMessage").textContent.includes("30 phút")')
        player.screenshot(path="/tmp/ott-tournament-player.png",full_page=True)
        admin.screenshot(path="/tmp/ott-tournament-admin.png",full_page=True)
        assert not errors, errors
        browser.close()
    engine.dispose()
    print("PASS: browser registration/join, organizer lifecycle, bot quota, real worker batch, replay, and appeal gate; no JS errors")


if __name__ == "__main__":
    run()
