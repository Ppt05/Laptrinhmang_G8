"""Verify admin session expiry and HTTP failures; all API traffic is mocked."""
import base64
import json
import os
from datetime import datetime, timedelta, timezone

from playwright.sync_api import expect, sync_playwright


def token(expiry):
    payload=json.dumps({"sub":"admin","exp":int(expiry.timestamp())}).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")+".fixture-signature"


def run():
    now=datetime.now(timezone.utc).replace(microsecond=0)
    errors=[]
    alerts={}
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)

        def open_admin(saved_token, failure=None):
            page=browser.new_page()
            alerts[page]=[]
            def accept_expiry(dialog):
                alerts[page].append({"type":dialog.type,"message":dialog.message})
                dialog.accept()
            page.on("dialog",accept_expiry)
            page.clock.install(time=now)
            page.on("pageerror",lambda error:errors.append(str(error)))
            page.add_init_script("sessionStorage.setItem('ottv2_admin_session', "+json.dumps(saved_token)+")")
            state={"failure":failure,"login_token":token(now+timedelta(hours=8)),"bad_login":False}
            def respond(route):
                path=route.request.url.split("/api/",1)[1]
                status=200
                if path=="admin/login":
                    status=401 if state["bad_login"] else 200
                    data={"detail":"Tên đăng nhập hoặc mật khẩu không đúng"} if state["bad_login"] else {
                        "access_token":state["login_token"],"expires_in":28800,"token_type":"bearer"}
                elif path=="admin/workers" and state["failure"]=="startup":
                    status,data=401,{"detail":"Phiên đăng nhập ban tổ chức không hợp lệ hoặc đã hết hạn"}
                elif path=="admin/workers" and state.get("delay_workers"):
                    state["pending_worker"]=route
                    return
                elif route.request.method!="GET":
                    status=state["failure"] or 200
                    data={"detail":"Fixture action rejected"} if status!=200 else {"id":1}
                elif path in ("tournaments","admin/workers"):data=[]
                else:raise AssertionError(path)
                route.fulfill(status=status,content_type="application/json",body=json.dumps(data))
            page.route("**/api/**",respond)
            page.goto(os.environ["TEST_BROWSER_URL"]+"/admin")
            return page,state

        def logged_out(page):
            expect(page.locator("#adminLoginPanel")).to_be_visible()
            expect(page.locator("#adminControls")).to_be_hidden()
            assert page.evaluate("sessionStorage.getItem('ottv2_admin_session')") is None
            expect(page.locator("#loginMessage")).to_contain_text("hết hạn")
            assert alerts[page]==[{"type":"alert","message":"Phiên đăng nhập đã hết hạn hoặc không còn hợp lệ. Vui lòng đăng nhập lại."}]

        # Server-side expiry or invalidation on the initial authenticated read.
        page,state=open_admin(token(now+timedelta(hours=1)),"startup")
        logged_out(page)
        page.close()

        # An old request cannot invalidate a newly established session.
        page,state=open_admin(token(now+timedelta(hours=1)))
        page.evaluate("refresh()")
        state["delay_workers"]=True
        with page.expect_request("**/api/admin/workers"):
            page.evaluate("window.oldRequestSettled=false; void api('/api/admin/workers').catch(()=>{}).finally(()=>window.oldRequestSettled=true)")
        page.locator("#adminLogout").click()
        state["delay_workers"]=False
        page.locator("#adminPassword").fill("fixture-password")
        page.locator('#adminLoginForm button[type="submit"]').click()
        expect(page.locator("#adminControls")).to_be_visible()
        state["pending_worker"].fulfill(status=401,content_type="application/json",body=json.dumps({"detail":"Old session expired"}))
        page.wait_for_function("window.oldRequestSettled")
        expect(page.locator("#adminControls")).to_be_visible()
        assert page.evaluate("sessionStorage.getItem('ottv2_admin_session')")==state["login_token"]
        assert not alerts[page]
        page.locator("#adminLogout").click()
        page.clock.fast_forward(9*3600*1000)
        expect(page.locator("#loginMessage")).to_be_empty()
        page.close()

        # Restored expired token must show login immediately.
        page,state=open_admin(token(now-timedelta(seconds=1)))
        logged_out(page)
        page.close()

        # Editing a form pauses polling, but must not postpone session expiry.
        page,state=open_admin(token(now+timedelta(seconds=10)))
        expect(page.locator("#adminControls")).to_be_visible()
        page.locator('#tournamentConfig [name="name"]').focus()
        page.clock.fast_forward(10001)
        logged_out(page)
        page.locator("#adminUsername").fill("admin")
        page.locator("#adminPassword").fill("fixture-password")
        page.locator('#adminLoginForm button[type="submit"]').click()
        expect(page.locator("#adminControls")).to_be_visible()
        expect(page.locator("#loginMessage")).to_be_empty()
        assert page.evaluate("sessionStorage.getItem('ottv2_admin_session')")==state["login_token"]
        page.clock.fast_forward(20000)
        expect(page.locator("#adminControls")).to_be_visible()
        page.close()

        # Auth failures on actions log out; permission, lifecycle and server errors do not.
        for status in (401,403,409,503):
            page,state=open_admin(token(now+timedelta(hours=1)),status)
            expect(page.locator("#adminControls")).to_be_visible()
            page.locator('#tournamentConfig button[type="submit"]').click()
            if status==401:logged_out(page)
            else:
                expect(page.locator("#adminMessage")).to_have_text("Fixture action rejected")
                expect(page.locator("#adminControls")).to_be_visible()
                assert page.evaluate("sessionStorage.getItem('ottv2_admin_session')")
                assert not alerts[page]
            page.close()

        # Failed login keeps its credential error rather than reporting session expiry.
        page,state=open_admin(token(now-timedelta(seconds=1)))
        logged_out(page)
        state["bad_login"]=True
        page.locator("#adminPassword").fill("wrong-password")
        page.locator('#adminLoginForm button[type="submit"]').click()
        expect(page.locator("#loginMessage")).to_have_text("Tên đăng nhập hoặc mật khẩu không đúng")
        expect(page.locator("#adminLoginPanel")).to_be_visible()
        page.close()
        assert not errors,errors
        browser.close()
    print("PASS: alert then logout on restored/idle expiry and 401; re-login, stale 401, credential/non-auth errors; APIs mocked")


if __name__=="__main__":
    run()
