"""Check tournament-only leaderboard and disabled submit explanations; APIs mocked."""
import json
import os
from datetime import datetime,timedelta,timezone
from urllib.parse import urlsplit,parse_qs

from playwright.sync_api import expect,sync_playwright


def run():
    now=datetime.now(timezone.utc)
    history=[{"id":2,"name":"Current Cup","status":"groups"},{"id":1,"name":"Old Cup","status":"complete"}]
    state={"reason":"","can_submit":True,"status_error":False}
    queries=[];errors=[]
    def tournament(tid):
        return {"id":tid,"name":"Current Cup" if tid==2 else "Old Cup","status":"groups" if tid==2 else "complete",
                "max_group_size":4,"qualifiers":2,"registration_open":now.isoformat(),"registration_close":now.isoformat(),
                "appeal_contact":"","incident_policy":"","roster":[],"groups":[],"encounters":[],"draws":[],
                "bracket":None,"notices":[],"rules_digest":"fixture","environment":{},
                "rounds":[{"id":tid,"level":0,"status":"submission","opens_at":(now-timedelta(hours=1)).isoformat(),
                           "closes_at":(now+timedelta(hours=1)).isoformat()}]}
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={"width":1440,"height":1000})
        page.add_init_script('sessionStorage.setItem("ottv2_session","fixture-token")')
        page.on("pageerror",lambda error:errors.append(str(error)))
        def respond(route):
            url=urlsplit(route.request.url);path=url.path.removeprefix("/api/");status=200
            if path=="tournaments":data=history
            elif path in ("tournaments/1","tournaments/2"):data=tournament(int(path[-1]))
            elif path.endswith("/team/status"):
                if state["status_error"]:status,data=503,{"detail":"Fixture unavailable"}
                else:data={"enrolled":True,"enrollment_status":"enrolled","attempts_used":1,"selected_submission":1,
                           "locked_submission":None,"elo":1000,"eligibility_reason":"latest_ready",
                           "can_submit":state["can_submit"],"submission_block_reason":state["reason"]}
            elif path=="leaderboard":
                query=parse_qs(url.query);assert "tournament_id" in query
                tid=int(query["tournament_id"][0]);queries.append(tid)
                data=[{"player_id":tid,"name":"Current team" if tid==2 else "Historic team","points":1016,"elo":1016,"matches":1,"wins":1}]
            elif path=="me":data={"name":"Fixture team"}
            elif path in ("submissions","my/matches"):data=[]
            else:raise AssertionError(path)
            route.fulfill(status=status,content_type="application/json",body=json.dumps(data))
        page.route("**/api/**",respond)
        page.goto(os.environ["TEST_BROWSER_URL"])
        submit=page.locator('#submitForm button[type="submit"]')
        expect(submit).to_be_enabled()
        page.locator('[data-open-leaderboard]').first.click()
        select=page.locator("#leaderboardTournament")
        expect(select).to_have_value("2")
        expect(page.locator("#leaderboardBody")).to_contain_text("Current team")
        expect(page.locator("#leaderboardBody")).not_to_contain_text("Historic team")
        select.select_option("1")
        expect(page.locator("#leaderboardBody")).to_contain_text("Historic team")
        expect(page.locator("#leaderboardBody")).not_to_contain_text("Current team")
        page.evaluate("refreshLeaderboard()")
        expect(select).to_have_value("1")
        for width in (1440,375):
            page.set_viewport_size({"width":width,"height":1000})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.locator("#closeLeaderboard").click()
        for reason in ("Chưa tới giờ mở cửa sổ nộp bot.","Đã dùng hết 3 lượt nộp bot của vòng này.",
                       "Bot của vòng này đã khóa; cửa sổ nộp đã kết thúc.","Đội không có suất tham dự vòng thi này."):
            state.update(reason=reason,can_submit=False)
            page.evaluate("refreshTournament()")
            expect(submit).to_be_disabled()
            expect(submit).to_have_attribute("title",reason)
            help=page.locator("#submitButtonHelp")
            help.hover()
            expect(help).to_have_attribute("title",reason)
            expect(page.locator("#submitBlockedReason")).to_have_text(reason)
            expect(page.locator("#submitBlockedReason")).to_be_visible()
        state.update(reason="",can_submit=True)
        page.evaluate("refreshTournament()")
        expect(submit).to_be_enabled()
        expect(submit).to_have_attribute("title","")
        expect(page.locator("#submitBlockedReason")).to_be_hidden()
        state["status_error"]=True
        page.evaluate("refreshTournament()")
        expect(submit).to_be_disabled()
        expect(page.locator("#submitBlockedReason")).to_contain_text("Fixture unavailable")
        history.clear()
        page.evaluate("refreshTournament()")
        expect(page.locator("#submitBlockedReason")).to_contain_text("chưa mở giải")
        page.evaluate("refreshLeaderboard()")
        expect(select).to_be_disabled()
        expect(page.locator("#leaderboardBody tr")).to_have_count(0)
        assert 1 in queries and 2 in queries
        assert not errors,errors
        browser.close()
    print("PASS: tournament leaderboard dropdown/isolation, selection retention, disabled-submit hover/text, errors and mobile; APIs mocked")


if __name__=="__main__":run()
