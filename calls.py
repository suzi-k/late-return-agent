"""Pull at-risk rentals from the rental API and place outbound calls with ElevenLabs batch calling.

Dry run by default. --send places calls. With DEMO_PHONE set, only the top rental is called, at that number.
"""
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from el import api, env, rental_api, state

CALL_HOURS = range(8, 21)  # rental's local time; skipped for DEMO_PHONE


def call_vars(r):
    due = datetime.fromisoformat(r["due_at"]).astimezone(ZoneInfo(r["tz"]))
    return {"reservation_id": r["id"], "first_name": r["first_name"], "tz": r["tz"],
            "due_at": due.strftime("%a %b %d, %I:%M %p %Z"), "branch": r["branch"], "vehicle": r["vehicle"]}


def main(send):
    demo = os.environ.get("DEMO_PHONE")
    if send and not demo:
        sys.exit("Seed phone numbers are fictional. Set DEMO_PHONE to your own number to place a real call.")
    recipients = []
    for r in rental_api("GET", "/at-risk"):
        if datetime.now(ZoneInfo(r["tz"])).hour not in CALL_HOURS and not demo:
            print(f"skip {r['id']}: outside calling hours")
            continue
        recipients.append({"phone_number": demo or r["phone"], "conversation_initiation_client_data": {"dynamic_variables": call_vars(r)}})
    recipients = recipients[:1] if demo else recipients
    print(json.dumps(recipients, indent=2))
    if send and recipients:
        job = api("POST", "/batch-calling/submit", json={"call_name": f"late-returns-{datetime.now():%Y%m%d-%H%M}",
                  "agent_id": state()["agent_id"], "agent_phone_number_id": env("PHONE_NUMBER_ID"), "recipients": recipients})
        print(f"Batch {job['id']}: {job['status']}")


if __name__ == "__main__":
    main("--send" in sys.argv)
