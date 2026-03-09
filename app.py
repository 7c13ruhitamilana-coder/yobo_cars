from __future__ import annotations
import os
from datetime import date
from pathlib import Path
from typing import Any
import streamlit as st

# Access the secret
biz_id = st.secrets["BIZ_ID"]

st.write(f"Connected to Business ID: {biz_id}")

from flask import Flask, jsonify, render_template, request

try:
    import tomllib  # py3.11+
except Exception:  # pragma: no cover
    import tomli as tomllib  # py3.10

try:
    import requests
except Exception:
    requests = None

app = Flask(__name__, template_folder="templates", static_folder="static")

COMPANIES: dict[str, dict[str, Any]] = {
    "demo": {
        "company_name": "Smart Car Rentals",
        "assistant_name": "Yobo",
        "hero_title": "Rent Your Dream Drive",
        "hero_subtitle": "From sleek city sedans to spacious family SUVs, transparent pricing. No hidden fees.",
        "accent": "#ceb7bc",
        "currency": "INR",
        "hero_image": "/static/ferrari-sticker.png",
    }
}


def load_secrets() -> dict[str, Any]:
    path = Path(__file__).parent / ".streamlit" / "secrets.toml"
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


SECRETS = load_secrets()
SUPABASE_URL = SECRETS.get("SUPABASE_URL")
SUPABASE_KEY = SECRETS.get("SUPABASE_KEY")
DEFAULT_BIZ_ID = SECRETS.get("BIZ_ID")


def supabase_headers() -> dict[str, str]:
    return {
        "apikey": str(SUPABASE_KEY),
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def resolve_biz_id() -> str | None:
    q = str(request.args.get("biz_id", "")).strip()
    return q or DEFAULT_BIZ_ID


def get_company_config() -> dict[str, Any]:
    key = str(request.args.get("company", "demo")).lower()
    cfg = COMPANIES.get(key, COMPANIES["demo"]).copy()
    if request.args.get("company_name"):
        cfg["company_name"] = request.args.get("company_name")
    if request.args.get("assistant"):
        cfg["assistant_name"] = request.args.get("assistant")
    if request.args.get("accent"):
        cfg["accent"] = request.args.get("accent")
    if request.args.get("currency"):
        cfg["currency"] = request.args.get("currency")
    return cfg


def fetch_available_fleet(biz_id: str, start_date: str, end_date: str) -> tuple[list[dict[str, Any]], str | None]:
    if not requests:
        return [], "requests package not installed"
    if not SUPABASE_URL or not SUPABASE_KEY:
        return [], "Supabase is not configured"

    fleet_res = requests.get(
        f"{SUPABASE_URL}/rest/v1/fleet",
        headers=supabase_headers(),
        params={
            "select": "id,make,model,price_per_day,available,photo_url",
            "biz_id": f"eq.{biz_id}",
            "available": "eq.true",
        },
        timeout=12,
    )
    if fleet_res.status_code != 200:
        return [], f"Fleet fetch failed: {fleet_res.text}"

    fleet_rows = fleet_res.json() or []
    if not fleet_rows:
        return [], None

    bookings_res = requests.get(
        f"{SUPABASE_URL}/rest/v1/bookings",
        headers=supabase_headers(),
        params={
            "select": "car_id,start_date,end_date",
            "biz_id": f"eq.{biz_id}",
            "start_date": f"lte.{end_date}",
            "end_date": f"gte.{start_date}",
        },
        timeout=12,
    )
    if bookings_res.status_code != 200:
        return [], f"Bookings check failed: {bookings_res.text}"

    booked_ids: set[int] = set()
    for row in (bookings_res.json() or []):
        car_id = row.get("car_id")
        try:
            if car_id is not None:
                booked_ids.add(int(car_id))
        except (TypeError, ValueError):
            continue

    available = [car for car in fleet_rows if int(car.get("id", -1)) not in booked_ids]
    return available, None


def car_still_available(biz_id: str, car_id: int, start_date: str, end_date: str) -> tuple[bool, str | None]:
    if not requests:
        return False, "requests package not installed"
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False, "Supabase is not configured"

    res = requests.get(
        f"{SUPABASE_URL}/rest/v1/bookings",
        headers=supabase_headers(),
        params={
            "select": "id",
            "biz_id": f"eq.{biz_id}",
            "car_id": f"eq.{car_id}",
            "start_date": f"lte.{end_date}",
            "end_date": f"gte.{start_date}",
            "limit": "1",
        },
        timeout=12,
    )
    if res.status_code != 200:
        return False, f"Availability check failed: {res.text}"

    return len(res.json() or []) == 0, None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config")
def api_config():
    return jsonify(get_company_config())


@app.route("/api/fleet")
def api_fleet():
    biz_id = resolve_biz_id()
    start_date = str(request.args.get("start_date", "")).strip()
    end_date = str(request.args.get("end_date", "")).strip()

    if not biz_id:
        return jsonify({"ok": False, "error": "Set BIZ_ID in .streamlit/secrets.toml"}), 400
    if not start_date or not end_date:
        return jsonify({"ok": False, "error": "start_date and end_date are required"}), 400

    try:
        d1 = date.fromisoformat(start_date)
        d2 = date.fromisoformat(end_date)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid date format (use YYYY-MM-DD)"}), 400

    if d2 < d1:
        return jsonify({"ok": False, "error": "End date must be on or after start date"}), 400

    rows, err = fetch_available_fleet(biz_id, start_date, end_date)
    if err:
        return jsonify({"ok": False, "error": err}), 500
    return jsonify({"ok": True, "data": rows})


@app.route("/api/estimate", methods=["POST"])
def api_estimate():
    body = request.get_json(force=True, silent=True) or {}
    start_date = str(body.get("start_date", "")).strip()
    end_date = str(body.get("end_date", "")).strip()
    price_per_day = int(body.get("price_per_day", 0) or 0)

    try:
        d1 = date.fromisoformat(start_date)
        d2 = date.fromisoformat(end_date)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid date format (use YYYY-MM-DD)"}), 400

    if d2 < d1:
        return jsonify({"ok": False, "error": "End date must be on or after start date"}), 400

    days = max((d2 - d1).days, 1)
    total = max(price_per_day, 0) * days
    return jsonify({"ok": True, "days": days, "estimated_total": total})


@app.route("/api/bookings", methods=["POST"])
def api_bookings():
    body = request.get_json(force=True, silent=True) or {}
    required = ["customer_name", "phone", "city", "start_date", "end_date", "car_id", "total_price"]
    missing = [k for k in required if body.get(k) in (None, "", [])]
    if missing:
        return jsonify({"ok": False, "error": f"Missing fields: {', '.join(missing)}"}), 400

    biz_id = resolve_biz_id()
    if not biz_id:
        return jsonify({"ok": False, "error": "Set BIZ_ID in .streamlit/secrets.toml"}), 400

    start_date = str(body["start_date"])
    end_date = str(body["end_date"])
    car_id = int(body["car_id"])

    ok_avail, err = car_still_available(biz_id, car_id, start_date, end_date)
    if err:
        return jsonify({"ok": False, "error": err}), 500
    if not ok_avail:
        return jsonify({"ok": False, "error": "This car has just been booked. Please choose another car."}), 409

    payload = {
        "biz_id": biz_id,
        "customer_name": body["customer_name"],
        "phone": body["phone"],
        "total_price": int(body["total_price"]),
        "start_date": start_date,
        "end_date": end_date,
        "city": body["city"],
        "car_id": car_id,
    }

    if not requests or not SUPABASE_URL or not SUPABASE_KEY:
        return jsonify({"ok": False, "error": "Supabase is not configured"}), 500

    res = requests.post(
        f"{SUPABASE_URL}/rest/v1/bookings",
        headers={**supabase_headers(), "Prefer": "return=representation"},
        json=payload,
        timeout=12,
    )
    if res.status_code in (200, 201):
        return jsonify({"ok": True, "message": "Booking confirmed."})

    return jsonify({"ok": False, "error": f"Booking insert failed: {res.text}"}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8510"))
    app.run(host="0.0.0.0", port=port, debug=True)

import os
