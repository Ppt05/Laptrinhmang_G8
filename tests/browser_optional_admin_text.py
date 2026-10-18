"""Check blank organizer text in real forms; all API requests are intercepted."""
import json
import os
from datetime import datetime, timedelta, timezone

from playwright.sync_api import expect, sync_playwright


def run():
    now=datetime.now(timezone.utc)
    state={"id":1,"name":"Optional text fixture","status":"registration","max_group_size":4,"qualifiers":2,
           "appeal_contact":"","incident_policy":"","registration_open":now.isoformat(),
           "registration_close":(now+timedelta(days=1)).isoformat(),"roster":[],"allocation":None,
           "bracket":None,"notices":[],"holds":[],"encounters":[],
           "rounds":[{"id":1,"level":0,"status":"planned","opens_at":None,"closes_at":None,"appeal_until":None}]}
    requests,errors=[],[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={"width":1440,"height":1000})
        page.on("pageerror",lambda error:errors.append(str(error)))
        page.add_init_script('sessionStorage.setItem("ottv2_admin_session","fixture-token")')

        def respond(route):
            path=route.request.url.split("/api/",1)[1]
            if route.request.method!="GET":
                requests.append({"path":path,"body":route.request.post_data_json})
                data={"id":1,"status":state["status"],"action_id":1}
            elif path=="tournaments": data=[{"id":1,"name":state["name"],"status":state["status"]}]
            elif path=="tournaments/1": data=state
            elif path.endswith("/audit"): data={"actions":[],"attempts":[]}
            elif path=="admin/workers": data=[]
            else: raise AssertionError(path)
            route.fulfill(status=200,content_type="application/json",body=json.dumps(data))

        page.route("**/api/**",respond)
        page.goto(os.environ["TEST_BROWSER_URL"]+"/admin")
        expect(page.locator("#adminControls")).to_be_visible()
        form=page.locator("#tournamentConfig")
        for name in ("name","appeal_contact","incident_policy"):
            field=form.locator(f'[name="{name}"]')
            field.fill("")
            assert not field.evaluate("element => element.required")
        assert form.evaluate("element => element.checkValidity()")
        form.locator('button[type="submit"]').click()
        expect(page.locator("#adminMessage")).to_have_text("Đã lưu thao tác.")
        assert requests[-1]["path"]=="admin/tournaments"
        assert all(requests[-1]["body"][name]=="" for name in ("name","appeal_contact","incident_policy"))
        count=len(requests)
        page.locator("#publishNotice").click()
        assert len(requests)==count
        exception=page.locator("#exceptionForm")
        assert exception.evaluate("element => element.checkValidity()")
        exception.locator('[name="kind"]').select_option("hold")
        with page.expect_response("**/api/admin/tournaments/1/rounds/1/exceptions/hold"):
            exception.locator('button[type="submit"]').click()
        assert requests[-1]["path"].endswith("/exceptions/hold")
        assert requests[-1]["body"]["reason"]==requests[-1]["body"]["evidence"]==""
        with page.expect_response("**/api/admin/tournaments/1/close"):
            page.locator("#closeTournament").click()
        assert requests[-1]["path"].endswith("/close")
        assert requests[-1]["body"]=={"reason":""}
        for width in (1440,375):
            page.set_viewport_size({"width":width,"height":1000})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors,errors
        browser.close()
    print("PASS: empty name/contact/policy/reasons/evidence submit; blank notice sends nothing; desktop/mobile; APIs mocked")


if __name__=="__main__":
    run()
