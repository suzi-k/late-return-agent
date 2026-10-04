"""Load rentals and flag the ones that can't make it back on time."""
import json
import pathlib
from datetime import datetime, timedelta, timezone

AVG_MPH, BUFFER_H = 50, 0.5  # simple drive-time estimate from simulated GPS distance
SEED = pathlib.Path(__file__).parent / "data" / "rentals.json"


def load(now=None):
    """Seed times are relative so the demo always has live at-risk rentals."""
    now = now or datetime.now(timezone.utc)
    rentals = {}
    for r in json.loads(SEED.read_text()):
        r["due_at"] = now + timedelta(hours=r.pop("due_in_h"))
        nb = r.pop("next_booking_in_h")
        r["next_booking_at"] = now + timedelta(hours=nb) if nb is not None else None
        rentals[r["id"]] = r
    return rentals


def assess(r, now):
    eta = now + timedelta(hours=r["miles_from_branch"] / AVG_MPH + BUFFER_H)
    late_h = (eta - r["due_at"]).total_seconds() / 3600
    conflict = r["next_booking_at"] is not None and eta > r["next_booking_at"]
    return {"late_h": round(late_h, 1), "conflict": conflict}


def at_risk(rentals, now=None):
    """Late rentals, those blocking another customer's booking first."""
    now = now or datetime.now(timezone.utc)
    flagged = [(r, assess(r, now)) for r in rentals.values()]
    flagged = [(r, a) for r, a in flagged if a["late_h"] > 0]
    return sorted(flagged, key=lambda x: (not x[1]["conflict"], -x[1]["late_h"]))
