"""Check named knockout rounds on player/admin pages; all API requests are mocked."""
import json
import os

from playwright.sync_api import expect, sync_playwright


def run():
    cases = {
        2: ["Chung kết"],
        4: ["Bán kết", "Chung kết"],
        8: ["Tứ kết", "Bán kết", "Chung kết"],
        16: ["Vòng 16 đội", "Tứ kết", "Bán kết", "Chung kết"],
        32: ["Vòng 32 đội", "Vòng 16 đội", "Tứ kết", "Bán kết", "Chung kết"],
    }
    errors = []
    state = {}

    def respond(route):
        assert route.request.method == "GET", "This test must not mutate tournament data"
        path = route.request.url.split("/api/", 1)[1]
        if path == "tournaments":
            data = [{"id": 1, "name": state["name"], "status": "knockout"}]
        elif path == "tournaments/1":
            data = state
        elif path.endswith("/audit"):
            data = {"actions": [], "attempts": []}
        elif path == "admin/workers":
            data = []
        elif path.startswith("leaderboard?"):
            data = []
        else:
            raise AssertionError(path)
        route.fulfill(status=200, content_type="application/json", body=json.dumps(data))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        player = browser.new_page()
        admin = browser.new_page()
        admin.add_init_script('sessionStorage.setItem("ottv2_admin_session", "fixture-token")')
        for page in (player, admin):
            page.route("**/api/**", respond)
            page.on("pageerror", lambda error: errors.append(str(error)))
        for size, names in cases.items():
            rounds = [{"id": 100, "level": 0, "status": "finalized"}]
            rounds += [{"id": level, "level": level, "status": "running"}
                       for level in range(1, len(names) + 1)]
            # Only two occupied slots: BYEs and vacancies must not rename the rounds.
            levels = {str(level): [{"a": 1 if i == 0 else None, "b": None, "bye": True}
                                  for i in range(size // (2 ** level))]
                      for level in range(1, len(names) + 1)}
            state.clear()
            state.update(id=1, name="Round names fixture", status="knockout",
                         max_group_size=4, qualifiers=2, appeal_contact="", incident_policy="",
                         registration_open=None, registration_close=None, holds=[], notices=[],
                         roster=[{"player_id": 1, "name": "Team A", "status": "enrolled"},
                                 {"player_id": 2, "name": "Team B", "status": "enrolled"}],
                         groups=[], draws=[], allocation=None, rules_digest="fixture", environment={},
                         bracket={"size": size, "seeds": [1, 2], "levels": levels}, rounds=rounds,
                         encounters=[{"id": level, "round_id": level, "level": level,
                                      "team_a": "Team A", "team_b": "Team B", "batch": 1,
                                      "status": "scheduled", "game_points": [0, 0],
                                      "encounter_points": [0, 0], "extra_scores": [], "games": []}
                                     for level in range(1, len(names) + 1)])
            player.goto(os.environ["TEST_BROWSER_URL"])
            expect(player.locator("#tournamentBracket h4")).to_have_text(names)
            expect(player.locator("#tournamentBracket")).to_contain_text("BYE")
            admin.goto(os.environ["TEST_BROWSER_URL"] + "/admin")
            options = admin.locator("#adminRound option")
            expect(options).to_have_count(len(names) + 1)
            expect(options.first).to_contain_text("Vòng bảng")
            for level, name in enumerate(names, 1):
                expect(options.nth(level)).to_contain_text(name)
                admin.locator("#adminRound").select_option(str(level))
                expect(admin.locator("#roundState")).to_contain_text(name)
                expect(admin.locator("#adminEncounters summary")).to_contain_text(name)
                expect(player.locator("#tournamentEncounters summary").nth(level - 1)).to_contain_text(name)
                state["rounds"] = rounds[:level + 1]
                player.evaluate("refreshTournament()")
                expect(player.locator("#tournamentSummary")).to_contain_text(name + " ·")
                expect(player.locator("#eventStatus")).to_contain_text(name + " · Bot:")
            for page in (player, admin):
                assert not page.get_by_text("Knockout cấp", exact=False).count()
        assert not errors, errors
        browser.close()
    print("PASS: player/admin round names for 2, 4, 8, 16, 32 slots, BYEs, vacant slots and group round; APIs mocked")


if __name__ == "__main__":
    run()
