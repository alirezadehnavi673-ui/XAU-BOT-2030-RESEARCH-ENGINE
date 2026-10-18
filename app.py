
from flask import Flask, render_template, jsonify, request
import os
import sqlite3
import json
import urllib.request
from datetime import datetime, timezone

app = Flask(__name__)

DB_PATH = os.environ.get("XAU_DB_PATH", "research.db")

YAHOO_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/"
    "DX-Y.NYB?range=5d&interval=1m"
)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lbma_reference (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session TEXT NOT NULL,
                lbma_price REAL NOT NULL,
                dxy_reference REAL,
                base_value REAL,
                captured_at TEXT NOT NULL,
                status TEXT NOT NULL
            )
        """)


def get_dxy():
    req = urllib.request.Request(
        YAHOO_URL,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    with urllib.request.urlopen(req, timeout=8) as response:
        data = json.loads(response.read().decode("utf-8"))

    result = data["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    closes = quote.get("close") or []

    valid = [v for v in closes if v is not None]

    if not valid:
        price = result.get("meta", {}).get("regularMarketPrice")
    else:
        price = valid[-1]

    if price is None:
        raise ValueError("DXY price unavailable")

    return {
        "price": float(price),
        "source": "Yahoo Finance public endpoint",
        "retrieved_at_utc": datetime.now(
            timezone.utc
        ).isoformat()
    }


@app.route("/")
def home():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "version": "1.2.0",
        "location_permission_required": False
    })


@app.get("/api/market")
def market():
    try:
        dxy = get_dxy()
        return jsonify({
            "ok": True,
            "dxy": dxy
        })
    except Exception as error:
        return jsonify({
            "ok": False,
            "dxy": None,
            "message": str(error),
            "status": "DATA_UNAVAILABLE"
        }), 200


@app.post("/api/reference")
def save_reference():
    data = request.get_json(silent=True) or {}

    session = str(data.get("session", "AM")).upper()

    if session not in ("AM", "PM"):
        return jsonify({
            "ok": False,
            "error": "Session must be AM or PM"
        }), 400

    try:
        lbma = float(data.get("lbma_price", 0))
    except (TypeError, ValueError):
        lbma = 0

    if lbma <= 0:
        return jsonify({
            "ok": False,
            "error": "Enter a valid LBMA price"
        }), 400

    captured_at = datetime.now(timezone.utc).isoformat()

    try:
        dxy_data = get_dxy()
        dxy = dxy_data["price"]
        base = lbma * dxy
        status = (
            "DXY captured on submission; "
            "official fixing-time match is not guaranteed"
        )
    except Exception:
        dxy = None
        base = None
        status = "DXY unavailable; base not calculated"

    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO lbma_reference
            (session, lbma_price, dxy_reference,
             base_value, captured_at, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            session, lbma, dxy, base,
            captured_at, status
        ))

        record_id = cursor.lastrowid

    return jsonify({
        "ok": True,
        "id": record_id,
        "session": session,
        "lbma_price": lbma,
        "dxy_reference": dxy,
        "base_value": base,
        "captured_at": captured_at,
        "status": status
    })


@app.get("/api/references")
def references():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM lbma_reference
            ORDER BY id DESC LIMIT 30
        """).fetchall()

    return jsonify([dict(row) for row in rows])


@app.get("/api/estimate")
def estimate():
    with get_db() as conn:
        row = conn.execute("""
            SELECT * FROM lbma_reference
            WHERE base_value IS NOT NULL
            ORDER BY id DESC LIMIT 1
        """).fetchone()

    if row is None:
        return jsonify({
            "ok": False,
            "estimated_xauusd": None,
            "message": "A valid LBMA/DXY reference is required"
        })

    try:
        current_dxy = get_dxy()["price"]

        if current_dxy <= 0:
            raise ValueError("Invalid DXY")

        estimated = row["base_value"] / current_dxy

        return jsonify({
            "ok": True,
            "lbma_session": row["session"],
            "lbma_price": row["lbma_price"],
            "reference_dxy": row["dxy_reference"],
            "base_value": row["base_value"],
            "current_dxy": current_dxy,
            "estimated_xauusd": estimated,
            "calculation": "base_value / current_dxy",
            "signal": "NOT_CONNECTED"
        })

    except Exception as error:
        return jsonify({
            "ok": False,
            "estimated_xauusd": None,
            "message": str(error)
        })


init_db()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
    )
