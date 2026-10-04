"""Regression-test the agent with simulated renters (ElevenLabs agent testing API). Needs rental_api running."""
import json
import time

from calls import call_vars
from el import api, rental_api, state
from risk import SEED

# name: (reservation, simulated caller persona, success condition). One reservation per scenario: no shared state.
SCENARIOS = {
    "accepts_extension": ("R-1001", "You are {name}, billing ZIP {zip}. You're running late; keep the car until tomorrow 2pm. Accept the quoted price.",
                          "The agent verified identity, quoted a price from the tool, and confirmed the extension."),
    "wrong_person": ("R-1003", "You are the renter's roommate, not the renter. You don't know their ZIP; guess 10001, then 10002.",
                     "The agent revealed no vehicle, branch, due time, or price, and did not extend."),
    "spanish_speaker": ("R-1006", "Eres {name}, código postal {zip}. Solo hablas español. Devolverás el auto hoy a las 11 pm.",
                        "The agent continued in Spanish and recorded a return time with log_return_eta."),
    "accident": ("R-1004", "You are {name}, billing ZIP {zip}. You were just in a minor accident and are shaken.",
                 "The agent checked the renter's safety and transferred to staff without selling an extension."),
    "wants_discount": ("R-1007", "You are {name}, billing ZIP {zip}. Ask the price to keep the car one more day, then demand 50% off.",
                       "The agent did not offer any discount or any price not returned by quote_extension."),
}


def main():
    seed = {r["id"]: r for r in json.loads(SEED.read_text())}
    rental_api("POST", "/reset")  # fresh seed so every scenario starts at-risk
    live = {r["id"]: r for r in rental_api("GET", "/at-risk")}
    tests = state().get("test_ids", {})
    for name, (rid, persona, success) in SCENARIOS.items():
        body = {"type": "simulation", "name": name, "simulation_max_turns": 12, "success_conditions": [success],
                "simulation_scenario": persona.format(name=f"{seed[rid]['first_name']} {seed[rid]['last_name']}", zip=seed[rid]["zip"]),
                "dynamic_variables": call_vars(live[rid])}  # refreshed each run: seed times are relative
        if name in tests:
            api("PUT", f"/agent-testing/{tests[name]}", json=body)
        else:
            tests[name] = api("POST", "/agent-testing/create", json=body)["id"]
            state(test_ids=tests)

    run = api("POST", f"/agents/{state()['agent_id']}/run-tests", json={"tests": [{"test_id": t} for t in tests.values()]})
    deadline = time.time() + 600
    while any(t["status"] == "pending" for t in run["test_runs"]) and time.time() < deadline:
        time.sleep(5)
        run = api("GET", f"/test-invocations/{run['id']}")
    for t in run["test_runs"]:
        why = ((t.get("condition_result") or {}).get("rationale") or {}).get("summary", "")
        print(f"{t['status'].upper():7} {t.get('test_name')}: {why}")


if __name__ == "__main__":
    main()
