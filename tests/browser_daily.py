"""UI smoke test against seeded local disposable deployment only."""
import os, uuid
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect

def run():
    url=os.environ['TEST_BROWSER_URL'].rstrip('/')
    assert urlparse(url).hostname in ('localhost','127.0.0.1') and urlparse(url).port!=8000
    errors=[]
    with sync_playwright() as p:
        launch={'headless':True}
        if os.getenv('TEST_BROWSER_EXECUTABLE'):launch['executable_path']=os.environ['TEST_BROWSER_EXECUTABLE']
        browser=p.chromium.launch(**launch)
        admin=browser.new_page(viewport={'width':1440,'height':1100})
        player=browser.new_page(viewport={'width':1440,'height':1000})
        for page in (admin,player):page.on('pageerror',lambda e:errors.append(str(e)))
        admin.goto(url+'/admin')
        admin.locator('#adminPassword').fill(os.environ['TEST_ADMIN_PASSWORD'])
        admin.locator('#adminLoginForm button').click()
        expect(admin.locator('#dailyEnabled')).to_be_enabled()
        with admin.expect_response(lambda r:r.url.endswith('/api/admin/daily') and r.request.method=='PUT') as saved:
            admin.locator('#dailyEnabled').uncheck()
        assert saved.value.ok and saved.value.json()['enabled'] is False
        expect(admin.locator('#dailyControlStatus')).to_have_text('Tạm đóng')
        assert admin.locator('#dailyControlForm button').count()==0
        assert admin.locator('#dailyEnabled').bounding_box()['width']<=52
        player.goto(url);player.locator('#modeDaily').click()
        expect(player.locator('#dailyState')).to_contain_text('Daily đang tắt')
        with admin.expect_response(lambda r:r.url.endswith('/api/admin/daily') and r.request.method=='PUT'):
            admin.locator('#dailyEnabled').check()
        expect(admin.locator('#dailyControlStatus')).to_have_text('Đang mở')
        player.locator('#dailyRefresh').click()
        expect(player.locator('#dailyState')).to_contain_text('Daily đang mở')
        # A failed save rolls the switch back and reports the error.
        admin.route('**/api/admin/daily',lambda route:route.fulfill(status=503,content_type='application/json',body='{"detail":"Test unavailable"}') if route.request.method=='PUT' else route.continue_())
        admin.locator('#dailyEnabled').uncheck()
        expect(admin.locator('#dailyAdminMessage')).to_contain_text('Chưa lưu được')
        expect(admin.locator('#dailyEnabled')).to_be_checked()
        admin.unroute('**/api/admin/daily')
        # Tables, pagination, human names, collapsed technical evidence.
        expect(admin.locator('#dailyAdminMatches tbody tr')).to_have_count(10)
        admin.locator('#dailyMoreMatches').click()
        expect(admin.locator('#dailyAdminMatches tbody tr')).to_have_count(13)
        assert 'Xem lại' in admin.locator('#dailyAdminMatches').inner_text()
        assert admin.locator('#dailyAdminMatches [data-daily-void]:visible').count()==0
        admin.locator('#dailyDraws').locator('..').locator('> summary').click()
        expect(admin.locator('#dailyDraws table').first).to_be_visible()
        assert 'Browser Daily Team' in admin.locator('#dailyDraws').inner_text()
        assert admin.locator('#dailyDraws pre:visible').count()==0
        admin.locator('#dailyDraws .daily-technical summary').first.click()
        expect(admin.locator('#dailyDraws pre').first).to_be_visible()
        # New account: five submissions, no automatic search.
        player.locator('#navRegister').click()
        player.locator('#registerForm [name="name"]').fill('Daily UI '+uuid.uuid4().hex[:8])
        player.locator('#registerForm [name="password"]').fill('DailyUITest2026!')
        player.locator('#registerForm button').click()
        expect(player.locator('#accountDialog')).not_to_be_visible()
        expect(player.locator('#dailyForm button')).to_be_enabled()
        player.locator('#dailyForm button').click()
        expect(player.locator('#dailyMessage')).to_contain_text('Đã nhận bot Daily')
        expect(player.locator('#dailyQuota')).to_contain_text('Bài nộp: 1/5')
        expect(player.locator('#dailyForm button')).to_be_enabled()
        expect(player.locator('#dailyFind')).to_be_disabled()
        expect(player.locator('#dailyCancel')).not_to_be_visible()
        player.locator('#navAccount').click();player.locator('#logoutBtn').click()
        player.locator('#navLogin').click()
        # Seeded ready team: choose an earlier bot, find once, cancel without quota use.
        player.locator('#loginForm [name="name"]').fill('Daily Ready Browser')
        player.locator('#loginForm [name="password"]').fill('DailyUITest2026!')
        player.locator('#loginForm button').click()
        expect(player.locator('#accountDialog')).not_to_be_visible()
        expect(player.locator('#dailyFind')).to_be_enabled()
        options=player.locator('#dailyBotSelect option').evaluate_all('(options)=>options.map(o=>o.value)')
        player.locator('#dailyBotSelect').select_option(options[-1])
        player.locator('#dailyFind').click()
        expect(player.locator('#dailyState')).to_contain_text('Đang tìm đối thủ')
        expect(player.locator('#dailyBotSelect')).to_have_value(options[-1])
        expect(player.locator('#dailyBotSelect')).to_be_disabled()
        player.locator('#dailyCancel').click()
        expect(player.locator('#dailyFind')).to_be_enabled()
        expect(player.locator('#dailyQuota')).to_contain_text('Trận: 0/5')
        player.locator('#modePractice').click();expect(player.locator('#dailyPanel')).not_to_be_visible()
        player.locator('#modeDaily').click();expect(player.locator('#dailyPanel')).to_be_visible()
        for page in (admin,player):
            page.set_viewport_size({'width':390,'height':844})
            page.emulate_media(reduced_motion='reduce')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),'Mobile overflow'
        assert admin.locator('#dailyAdminMatches tbody tr').first.evaluate('(e)=>getComputedStyle(e).display')=='block'
        if os.getenv('TEST_SCREENSHOT'):
            admin.locator('.daily-admin-panel').screenshot(path=os.environ['TEST_SCREENSHOT'],animations='disabled')
        assert not errors,errors
        browser.close()
    print('Daily UI passed: switch/autosave/rollback, tables/pagination/names, technical disclosure, 5-submission quota, explicit search/cancel/bot choice, mobile cards')

if __name__=='__main__':run()
