"""Check conditional Force controls with intercepted API responses; no DB writes.

Set TEST_BROWSER_URL and PLAYWRIGHT_BROWSERS_PATH. All /api requests are mocked.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from playwright.sync_api import expect, sync_playwright


def run():
    now=datetime.now(timezone.utc)
    past=(now-timedelta(hours=1)).isoformat()
    future=(now+timedelta(hours=1)).isoformat()
    state={"id":1,"name":"UI fixture","status":"groups","max_group_size":4,"qualifiers":2,
           "appeal_contact":"test@example.test","incident_policy":"Test fixture only",
           "registration_open":past,"registration_close":future,"roster":[],"allocation":None,
           "bracket":None,"notices":[],"holds":[],"encounters":[],
           "rounds":[{"id":1,"level":0,"status":"submission","opens_at":past,"closes_at":future,"appeal_until":None}]}
    requests=[]
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={"width":1440,"height":1000})
        page.on("pageerror",lambda error:errors.append(str(error)))
        page.add_init_script('sessionStorage.setItem("ottv2_admin_session","fixture-token")')
        def respond(route):
            path=route.request.url.split("/api/",1)[1]
            if route.request.method!="GET":
                requests.append({"path":path,"body":route.request.post_data_json})
                data={"id":1,"status":"running"}
            elif path=="tournaments": data=[{"id":1,"name":state["name"],"status":state["status"]}]
            elif path=="tournaments/1": data=state
            elif path.endswith("/audit"): data={"actions":[],"attempts":[]}
            elif path=="admin/workers": data=[]
            else: raise AssertionError(path)
            route.fulfill(status=200,content_type="application/json",body=json.dumps(data))
        page.route("**/api/**",respond)
        page.goto(os.environ["TEST_BROWSER_URL"]+"/admin")
        expect(page.locator("#adminControls")).to_be_visible()
        expect(page.locator("[data-force-action]")).to_have_count(0)
        expect(page.locator("#forceWindow")).to_have_count(0)
        expect(page.locator('[data-force-round-action="start-batch"]')).to_have_count(0)
        expect(page.locator('[data-force-round-action="qualify"]')).to_have_count(0)
        expect(page.locator("#batchNumber")).to_have_count(0)
        force_open=page.locator("#forceOpenWindow")
        expect(force_open).to_be_hidden()
        state["rounds"][0]["opens_at"]=future
        page.evaluate("refresh()")
        expect(force_open).to_be_visible()
        force_open.click()
        page.evaluate("refresh()")
        assert requests[-1]["path"].endswith("/window")
        assert requests[-1]["body"]["force"] is True
        assert datetime.fromisoformat(requests[-1]["body"]["opens_at"].replace("Z","+00:00"))<=datetime.now(timezone.utc)
        state["rounds"][0]["opens_at"]=past
        page.evaluate("refresh()")
        expect(force_open).to_be_hidden()
        lock=page.locator('[data-force-round-action="lock-bots"]')
        encounter=page.locator('[data-force-round-action="finalize-encounters"]')
        final=page.locator('[data-force-round-action="finalize"]')
        expect(lock).to_be_visible()
        expect(page.locator("#forceControls")).to_be_visible()
        page.locator("#forceReason").fill("Skip waiting in UI fixture")
        lock.click()
        page.evaluate("refresh()")
        assert requests[-1]["body"]["force"] is True
        assert requests[-1]["body"]["force_reason"]=="Skip waiting in UI fixture"
        r=state["rounds"][0]
        r["closes_at"]=past
        page.evaluate("refresh()")
        expect(lock).to_be_hidden()
        expect(force_open).to_be_visible()
        r["closes_at"]=future
        page.evaluate("refresh()")
        expect(force_open).to_be_hidden()
        r["closes_at"]=past
        r["status"]="planned"
        page.evaluate("refresh()")
        expect(page.locator("#forceControls")).to_be_hidden()
        r["status"]="locked"
        expect(force_open).to_be_hidden()
        state["encounters"]=[{"id":1,"round_id":1,"team_a":"Team A","team_b":"Team B","batch":2,
            "status":"scheduled","game_points":[0,0],"encounter_points":[0,0],"extra_scores":[],
            "reason":None,"games":[],"appeal_until":None}]
        page.evaluate("refresh()")
        expect(page.locator("#nextBatch")).to_have_text("Lượt tiếp theo: 2")
        page.locator('[data-round-action="start-batch"]').click()
        page.evaluate("refresh()")
        assert "force" not in requests[-1]["body"]
        assert "batch" not in requests[-1]["body"]
        assert requests[-1]["path"].endswith("/start-batch")
        e={"id":1,"round_id":1,"team_a":"Team A","team_b":"Team B","batch":1,
           "status":"provisional","game_points":[2,0],"encounter_points":[3,0],
           "extra_scores":[],"reason":"played","games":[],"appeal_until":future}
        state["encounters"]=[e];r["status"]="running"
        page.evaluate("refresh()")
        expect(encounter).to_be_visible()
        expect(final).to_be_hidden()
        expect(page.locator("[data-force-finalize]")).to_have_count(1)
        e["appeal_until"]=past
        page.evaluate("refresh()")
        expect(encounter).to_be_hidden()
        expect(page.locator("[data-force-finalize]")).to_have_count(0)
        state["holds"]=[{"round_id":1,"encounter_id":1,"resolved_at":None,"appeal_until":None}]
        page.evaluate("refresh()")
        expect(encounter).to_be_visible()
        state["holds"][0]["resolved_at"]=past
        page.evaluate("refresh()")
        expect(encounter).to_be_hidden()
        state["holds"]=[]; e["status"]="finalized";r["status"]="provisional";r["appeal_until"]=future
        page.evaluate("refresh()")
        expect(final).to_be_visible()
        r["appeal_until"]=past
        page.evaluate("refresh()")
        expect(final).to_be_hidden()
        r["level"]=1;r["status"]="running";e["status"]="provisional";e["appeal_until"]=future
        page.evaluate("refresh()")
        expect(final).to_be_hidden()
        expect(encounter).to_be_hidden()
        state["holds"]=[{"round_id":1,"encounter_id":1,"resolved_at":None,"appeal_until":None}]
        page.evaluate("refresh()")
        expect(final).to_be_visible()
        expect(encounter).to_be_visible()
        state["holds"]=[]
        e["status"]="running"
        page.evaluate("refresh()")
        expect(final).to_be_hidden()
        assert not errors,errors
        browser.close()
    print("PASS: Force appears only for blocked bot locks and result finalization; normal run has no force flag; all API requests intercepted")


if __name__=="__main__":
    run()
