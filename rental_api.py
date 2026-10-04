"""Mock rental system: tool endpoints the agent calls mid-call, the post-call webhook, and the ops report."""
import hashlib
import hmac
import html
import json
import math
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, abort, jsonify, make_response, request

import risk
from el import env

TOOL_TOKEN, WEBHOOK_SECRET, REPORT_KEY = env("TOOL_TOKEN"), env("WEBHOOK_SECRET"), env("REPORT_KEY")
MAX_FAILS, MAX_DAYS = 3, 7
REACHED = {"extended", "returning_on_time", "returning_late", "escalated"}

app = Flask(__name__)
RENTALS, FLAGGED = {}, set()  # FLAGGED: at-risk at load, so extended rentals stay on the report
VERIFIED, FAILS, OUTCOMES = set(), {}, {}  # (conversation_id, reservation_id) / failed ZIPs per reservation / call results


def same(given, expected):
    return hmac.compare_digest(str(given).encode(), expected.encode())


def fail(error, code):
    abort(make_response(jsonify(error=error), code))


def authed():
    if not same(request.headers.get("X-Api-Key", ""), TOOL_TOKEN):
        abort(401)


def ctx(rid, need_verified=True):
    authed()
    r = RENTALS.get(rid) or abort(404)
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    if not isinstance(body.get("conversation_id"), str) or not body["conversation_id"]:
        fail("conversation_id required", 400)
    if need_verified and (body["conversation_id"], rid) not in VERIFIED:
        fail("Renter not verified. Do not share rental details.", 403)
    return r, body


def parse_time(r, value):
    try:
        t = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        fail("Use ISO 8601, e.g. 2026-10-05T14:00:00-07:00", 400)
    return t if t.tzinfo else t.replace(tzinfo=ZoneInfo(r["tz"]))


def price(r, new_time):
    days = math.ceil((new_time - r["due_at"]).total_seconds() / 86400)
    if days < 1:
        fail("New time must be after the current due time.", 400)
    if days > MAX_DAYS:
        fail(f"Extensions over {MAX_DAYS} days need staff. Offer a transfer.", 400)
    return {"extra_days": days, "price_usd": days * r["daily_rate"], "new_return_time": new_time.isoformat()}


def blocks_next_booking(r, t):
    return r["next_booking_at"] is not None and t > r["next_booking_at"]


@app.post("/rentals/<rid>/verify")
def verify(rid):
    r, b = ctx(rid, need_verified=False)
    if FAILS.get(rid, 0) >= MAX_FAILS:
        return {"verified": False, "locked": True, "instruction": "Say you can't discuss the rental and they can call the branch."}
    if not same(str(b.get("zip", "")).strip(), r["zip"]):
        FAILS[rid] = FAILS.get(rid, 0) + 1
        return {"verified": False}
    VERIFIED.add((b["conversation_id"], rid))
    return {"verified": True}


@app.post("/rentals/<rid>/quote")
def quote(rid):
    r, b = ctx(rid)
    return price(r, parse_time(r, b.get("new_return_time")))


@app.post("/rentals/<rid>/extend")
def extend(rid):
    r, b = ctx(rid)
    new_time = parse_time(r, b.get("new_return_time"))
    q = price(r, new_time)
    r.update(due_at=new_time, needs_reassign=blocks_next_booking(r, new_time), extension_usd=r.get("extension_usd", 0) + q["price_usd"])
    return {**q, "status": "extended", "charged_to": "card on file"}


@app.post("/rentals/<rid>/eta")
def eta(rid):
    r, b = ctx(rid)
    t = parse_time(r, b.get("eta"))
    r.update(eta=t.isoformat(), needs_reassign=blocks_next_booking(r, t))
    return {"status": "recorded", "eta": r["eta"]}


@app.get("/at-risk")
def at_risk():
    authed()
    return [{**{k: r[k] for k in ("id", "first_name", "vehicle", "branch", "tz", "phone")},
             "due_at": r["due_at"].isoformat(), **a} for r, a in risk.at_risk(RENTALS)]


def load_seed():
    for store in (RENTALS, FLAGGED, VERIFIED, FAILS, OUTCOMES):
        store.clear()
    RENTALS.update(risk.load())
    FLAGGED.update(r["id"] for r, _ in risk.at_risk(RENTALS))


@app.post("/reset")
def reset():
    authed()
    load_seed()
    return {"ok": True}


@app.post("/webhooks/elevenlabs")
def post_call():
    """HMAC check (header t=<ts>,v0=<sha256 of "ts.body">, 30-min window), then store the call's collected data."""
    parts = dict(p.split("=", 1) for p in request.headers.get("elevenlabs-signature", "").split(",") if "=" in p)
    ts, raw = parts.get("t", ""), request.get_data(as_text=True)
    expected = hmac.new(WEBHOOK_SECRET.encode(), f"{ts}.{raw}".encode(), hashlib.sha256).hexdigest()
    if not ts.isdigit() or abs(time.time() - int(ts)) > 1800 or not same(parts.get("v0", ""), expected):
        abort(401)
    try:
        event = json.loads(raw)
    except ValueError:
        fail("invalid JSON", 400)
    get = lambda d, k: d.get(k) if isinstance(d, dict) else None
    data = get(event, "data")
    rid = get(get(get(data, "conversation_initiation_client_data"), "dynamic_variables"), "reservation_id")
    if get(event, "type") != "post_call_transcription" or not isinstance(rid, str) or rid not in RENTALS:
        return {"ok": True, "ignored": True}
    results = get(get(data, "analysis"), "data_collection_results")
    OUTCOMES[rid] = {k: get(v, "value") for k, v in results.items()} if isinstance(results, dict) else {}
    return {"ok": True}


@app.get("/report")
def report():
    if not same(request.args.get("key", ""), REPORT_KEY):  # read-only key, safe to put in a browser URL
        abort(401)
    rows = [(rid, OUTCOMES.get(rid, {}), RENTALS[rid]) for rid in sorted(FLAGGED | {r["id"] for r, _ in risk.at_risk(RENTALS)})]
    kpis = {
        "At-risk rentals": len(rows),
        "Renters reached": sum(o.get("outcome") in REACHED for _, o, _ in rows),
        "Extensions sold": sum(bool(r.get("extension_usd")) for *_, r in rows),
        "Revenue added": f"${sum(r.get('extension_usd', 0) for *_, r in rows):,}",
        "Escalated to staff": sum(bool(o.get("escalated")) for _, o, _ in rows),
        "Bookings to reassign": sum(bool(r.get("needs_reassign")) for *_, r in rows),
    }
    cell = lambda v: f"<td>{html.escape(str(v if v not in (None, '') else '—'))}</td>"
    body = "".join(f"<tr>{cell(rid)}{cell(r['vehicle'])}{cell(r['branch'])}{cell(o.get('outcome'))}"
                   f"{cell(r.get('eta') or o.get('new_return_time'))}{cell(r.get('extension_usd'))}</tr>" for rid, o, r in rows)
    tiles = "".join(f"<div><b>{v}</b>{k}</div>" for k, v in kpis.items())
    return f"""<!doctype html><meta charset=utf-8><title>Late returns</title><style>
body{{font:15px system-ui;margin:2rem}} .k{{display:flex;gap:1rem;flex-wrap:wrap}} .k div{{border:1px solid #ddd;border-radius:8px;padding:.8rem 1rem}}
.k b{{display:block;font-size:1.6rem}} table{{border-collapse:collapse;margin-top:1.5rem;width:100%}} td,th{{border-bottom:1px solid #eee;padding:.5rem;text-align:left}}
</style><h1>Late returns, Northwind Rentals (fictional)</h1><div class=k>{tiles}</div>
<table><tr><th>Reservation</th><th>Vehicle</th><th>Branch</th><th>Outcome</th><th>New return / ETA</th><th>Extension $</th></tr>{body}</table>"""


load_seed()
if __name__ == "__main__":
    app.run(port=8000)
