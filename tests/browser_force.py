"""Opt-in deadline override verification with an empty disposable API and real worker.

Set TEST_BROWSER_URL, TEST_ADMIN_PASSWORD and PLAYWRIGHT_BROWSERS_PATH.
The worker must use the same disposable database and separate artifacts.
"""
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def run():
    url=os.environ["TEST_BROWSER_URL"]
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        admin=browser.new_page(viewport={"width":1440,"height":1100})
        admin.on("pageerror",lambda error:errors.append(str(error)))
        response=admin.request.get(url+"/api/tournaments")
        assert response.ok and response.json()==[], "Use an empty disposable deployment"
        admin.goto(url+"/admin")
        admin.locator("#adminPassword").fill(os.environ["TEST_ADMIN_PASSWORD"])
        admin.locator("#adminLoginForm button").click()
        expect(admin.locator("#adminControls")).to_be_visible()
        now=datetime.now(timezone.utc)
        zone=timezone(timedelta(hours=7))
        def local(value): return value.astimezone(zone).strftime("%Y-%m-%dT%H:%M")
        form=admin.locator("#tournamentConfig")
        form.locator('[name="name"]').fill("Force Browser Cup")
        form.locator('[name="registration_open"]').fill(local(now-timedelta(hours=1)))
        form.locator('[name="registration_close"]').fill(local(now+timedelta(days=365)))
        form.locator('[name="appeal_contact"]').fill("appeals@example.test")
        form.locator('[name="incident_policy"]').fill("Postpone affected round after 24 hours")
        form.locator('button[type="submit"]').click()
        admin.wait_for_function('tournament?.status === "registration"')
        tid=admin.evaluate("tournamentId")
        tokens=[]
        for i in range(2):
            response=admin.request.post(url+"/api/register",data={"name":f"Force Browser Team {i}","password":"force-team-password"})
            assert response.ok,response.text()
            token=response.json()["token"];tokens.append(token)
            response=admin.request.post(f"{url}/api/tournaments/{tid}/join",headers={"Authorization":"Bearer "+token})
            assert response.ok,response.text()
        admin.evaluate("refresh()")
        expect(admin.locator('[data-force-action]')).to_have_count(0)
        expect(admin.locator('[data-force-round-action="start-batch"]')).to_have_count(0)
        admin.locator('[data-action="lock-roster"]').click()
        admin.wait_for_function('tournament?.status === "allocated"')
        admin.locator('[data-action="confirm-groups"]').click()
        admin.wait_for_function('tournament?.status === "groups"')
        def open_window():
            admin.locator("#windowOpen").fill(local(now-timedelta(minutes=1)))
            admin.locator("#windowClose").fill(local(now+timedelta(days=365)))
            admin.locator('#windowForm button[type="submit"]').click()
            admin.wait_for_function('round()?.status === "submission"')
        open_window()
        admin.locator("#forceReason").fill("Browser verification on disposable database")
        rid=admin.evaluate("round().id")
        for i,token in enumerate(tokens):
            response=admin.request.post(f"{url}/api/tournaments/{tid}/rounds/{rid}/submissions",
                headers={"Authorization":"Bearer "+token,"Idempotency-Key":"force-browser-bot"},
                data={"language":"python","source":Path("examples/bot_greedy.py" if i else "examples/bot_first.py").read_text()})
            assert response.ok,response.text()
        for _ in range(90):
            statuses=[admin.request.get(url+"/api/submissions",headers={"Authorization":"Bearer "+token}).json()[0]["status"] for token in tokens]
            assert not any(status in ("failed","infrastructure_error") for status in statuses),statuses
            if statuses==["ready","ready"]: break
            time.sleep(1)
        else: raise AssertionError("Worker did not validate both bots")
        admin.locator('[data-round-action="lock-bots"]').click()
        expect(admin.locator("#adminMessage")).to_contain_text("Cần đóng cửa sổ")
        admin.locator('[data-force-round-action="lock-bots"]').click()
        admin.wait_for_function('round()?.status === "locked"')

        def public(): return admin.request.get(f"{url}/api/tournaments/{tid}").json()

        def drain(rid):
            for _ in range(180):
                entries=[e for e in public()["encounters"] if e["round_id"]==rid]
                assert not any(e["status"]=="paused" for e in entries),entries
                if entries and all(e["status"] in ("finalized","cancelled") for e in entries): return
                if any(e["status"]=="provisional" for e in entries):
                    admin.evaluate("refresh()")
                    admin.locator('[data-force-round-action="finalize-encounters"]').click()
                    admin.wait_for_function('!tournament.encounters.some(e=>e.round_id===round().id&&e.status==="provisional")')
                elif any(e["status"]=="scheduled" for e in entries):
                    admin.locator('[data-round-action="start-batch"]').click()
                    admin.wait_for_function('round()?.status === "running"')
                else: time.sleep(1)
            raise AssertionError("Real worker did not finish round")

        for _ in range(3):
            drain(rid)
            admin.evaluate("refresh()")
            admin.locator('[data-round-action="qualify"]').click()
            admin.wait_for_function('round()?.status === "provisional" || tournament.encounters.some(e=>e.status==="scheduled")')
            if admin.evaluate('round().status === "provisional"'): break
        else: raise AssertionError("Qualification did not finish after playoffs")
        admin.locator('[data-force-round-action="finalize"]').click()
        admin.wait_for_function('tournament?.status === "qualified"')
        admin.locator('[data-action="confirm-bracket"]').click()
        admin.wait_for_function('tournament?.status === "knockout"')
        admin.evaluate('document.querySelector("#adminRound").value=tournament.rounds.at(-1).id; paintRound()')
        open_window()
        admin.locator('[data-force-round-action="lock-bots"]').click()
        admin.wait_for_function('round()?.status === "locked"')
        rid=admin.evaluate("round().id")
        drain(rid)
        admin.evaluate("refresh()")
        admin.locator('[data-round-action="finalize"]').click()
        admin.wait_for_function('tournament?.status === "complete"')
        data=public()
        assert data["champion_id"],data
        games=[g for e in data["encounters"] for g in e["games"]]
        assert len(games)>=8 and all(g["status"]=="done" for g in games)
        for game in games:
            response=admin.request.get(f"{url}/api/matches/{game['id']}")
            assert response.ok,response.text()
        assert not errors,errors
        browser.close()
    print(f"PASS: force only for blocked deadlines; normal transitions, two real Python bots, {len(games)} sandbox games, replays and champion; no JavaScript errors")


if __name__=="__main__":
    run()
