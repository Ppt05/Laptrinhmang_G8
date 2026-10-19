"""Exercise automatic tournament controls; all API requests are intercepted."""
import json
import os
from datetime import datetime,timedelta,timezone
from playwright.sync_api import expect,sync_playwright


def run():
    now=datetime.now(timezone.utc)
    state={"id":1,"name":"Automatic fixture","status":"groups","max_group_size":4,"qualifiers":2,
           "registration_open":now.isoformat(),"registration_close":(now+timedelta(days=1)).isoformat(),
           "appeal_contact":"","incident_policy":"","auto_run":True,"auto_submission_minutes":15,
           "auto_status":"Chờ hết hạn khiếu nại encounter.","roster":[],"rounds":[],"holds":[],"notices":[],
           "allocation":None,"bracket":None,"encounters":[]}
    requests=[];errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page(viewport={"width":1440,"height":1000})
        page.add_init_script('sessionStorage.setItem("ottv2_admin_session","fixture-token")')
        page.on("pageerror",lambda error:errors.append(str(error)))
        def respond(route):
            path=route.request.url.split("/api/",1)[1]
            if route.request.method!="GET":
                body=route.request.post_data_json;requests.append({"path":path,"body":body})
                assert path.endswith("/automation")
                state.update(body);data=body
            elif path=="tournaments":data=[{"id":1,"name":state["name"],"status":state["status"]}]
            elif path=="tournaments/1":data=state
            elif path.endswith("/audit"):data={"actions":[],"attempts":[]}
            elif path=="admin/workers":data=[]
            else:raise AssertionError(path)
            route.fulfill(status=200,content_type="application/json",body=json.dumps(data))
        page.route("**/api/**",respond);page.goto(os.environ["TEST_BROWSER_URL"]+"/admin")
        expect(page.locator("#automationState")).to_contain_text(state["auto_status"])
        toggle=page.locator('[name="auto_run"]');minutes=page.locator('[name="auto_submission_minutes"]')
        expect(toggle).to_be_checked();expect(minutes).to_have_value("15")
        toggle.uncheck();minutes.fill("5")
        with page.expect_response("**/api/admin/tournaments/1/automation"):page.locator("#saveAutomation").click()
        page.evaluate("refresh()")
        assert requests[-1]["body"]=={"auto_run":False,"auto_submission_minutes":5}
        expect(page.locator("#automationState")).to_contain_text("tắt")
        toggle.check();minutes.fill("1")
        with page.expect_response("**/api/admin/tournaments/1/automation"):page.locator("#saveAutomation").click()
        page.evaluate("refresh()")
        assert requests[-1]["body"]=={"auto_run":True,"auto_submission_minutes":1}
        expect(page.locator("#automationState")).to_contain_text("bật")
        state["status"]="complete";page.evaluate("refresh()")
        expect(page.locator("#saveAutomation")).to_be_disabled()
        for width in (1440,375):
            page.set_viewport_size({"width":width,"height":1000})
            assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
            assert toggle.bounding_box()["width"]<=25
        assert not errors,errors;browser.close()
    print("PASS: automatic mode, duration, status, active-stage editing, completed guard, mobile; APIs mocked")


if __name__=="__main__":run()
