"""Offline checks for risk scoring and the rental API (no ElevenLabs calls). Run: python test_local.py"""
import hashlib
import hmac
import json
import os
import time
from datetime import timedelta

os.environ.update(TOOL_TOKEN="test-token", WEBHOOK_SECRET="test-secret", REPORT_KEY="test-report")
import rental_api  # noqa: E402

H = {"X-Api-Key": "test-token"}
c = rental_api.app.test_client()


def call(path, **body):
    return c.post(path, headers=H, json=body)


def due_plus(rid, hours):
    return (rental_api.RENTALS[rid]["due_at"] + timedelta(hours=hours)).isoformat()


def test_at_risk_order():
    ids = [r["id"] for r in c.get("/at-risk", headers=H).json]
    assert ids[:2] == ["R-1004", "R-1001"]  # blocking a next booking ranks first
    assert {"R-1002", "R-1005"}.isdisjoint(ids)  # these make it back on time


def test_verification():
    c.post("/reset", headers=H)
    assert c.get("/at-risk").status_code == 401
    assert call("/rentals/R-1001/verify", zip="94107").status_code == 400  # conversation_id required
    assert c.post("/rentals/R-1001/verify", headers=H, json=[1]).status_code == 400
    assert call("/rentals/R-1001/quote", conversation_id="x", new_return_time=due_plus("R-1001", 5)).status_code == 403
    assert call("/rentals/R-1003/verify", conversation_id="x", zip="11430").json["verified"]
    assert call("/rentals/R-1001/quote", conversation_id="x", new_return_time=due_plus("R-1001", 5)).status_code == 403  # per reservation
    for _ in range(3):
        assert not call("/rentals/R-1001/verify", conversation_id="y", zip="00000").json["verified"]
    assert call("/rentals/R-1001/verify", conversation_id="z", zip="94107").json["locked"]  # lockout survives a new call


def test_extend_and_report():
    c.post("/reset", headers=H)
    assert call("/rentals/R-1001/verify", conversation_id="y", zip="94107").json["verified"]
    assert call("/rentals/R-1001/quote", conversation_id="y", new_return_time=due_plus("R-1001", 24 * 8)).status_code == 400  # cap
    q = call("/rentals/R-1001/quote", conversation_id="y", new_return_time=due_plus("R-1001", 30)).json
    assert (q["extra_days"], q["price_usd"]) == (2, 128)
    assert call("/rentals/R-1001/extend", conversation_id="y", new_return_time=q["new_return_time"]).json["status"] == "extended"
    assert rental_api.RENTALS["R-1001"]["needs_reassign"]
    assert c.get("/report?key=test-token").status_code == 401  # tool token can't open the report
    page = c.get("/report?key=test-report").get_data(as_text=True)
    assert "$128" in page and "R-1001" in page


def test_webhook():
    def post(body, ts=None, sig=None):
        raw, ts = json.dumps(body), str(ts or int(time.time()))
        sig = sig or hmac.new(b"test-secret", f"{ts}.{raw}".encode(), hashlib.sha256).hexdigest()
        return c.post("/webhooks/elevenlabs", data=raw, headers={"elevenlabs-signature": f"t={ts},v0={sig}"})
    event = {"type": "post_call_transcription", "data": {"conversation_initiation_client_data": {"dynamic_variables": {"reservation_id": "R-1003"}},
                                                         "analysis": {"data_collection_results": {"outcome": {"value": "returning_late"}}}}}
    assert post(event, sig="bad").status_code == 401
    assert post(event, ts=int(time.time()) + 10**6).status_code == 401  # future timestamp
    assert post(event).status_code == 200 and rental_api.OUTCOMES["R-1003"]["outcome"] == "returning_late"
    assert post({**event, "type": "call_initiation_failure"}).json["ignored"]
    for junk in ({"type": "post_call_transcription", "data": [1]}, [], {"type": "post_call_transcription", "data": {
            "conversation_initiation_client_data": {"dynamic_variables": {"reservation_id": ["R-1003"]}}}}):
        assert post(junk).status_code == 200


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
