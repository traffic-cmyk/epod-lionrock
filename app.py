"""
ePOD System - Electronic Proof of Delivery
Lionrock Haulage | DVLA & HMRC Compliant
"""

from flask import Flask, request, jsonify, render_template, redirect, url_for
import sqlite3
import os
import json
from datetime import datetime

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), "epod.db")

# ─── Configuration ─────────────────────────────────────────────────────────────
# Edit these lists to match your fleet and drivers

VEHICLES = {
    # "REG": {"type": "HGV Artic", "driver": "Driver Name"}
    "BV19LFN": {"type": "HGV Artic", "driver": "Luca Chau"},
}

COLLECTION_ADDRESSES = [
    # Common pickup points — shown as quick-select presets
    # {"label": "Short name", "address": "Full address inc postcode"}
    {"label": "Custom", "address": ""},  # always keep this for manual entry
]

GOODS_PRESETS = [
    # Quick-tap common goods types
    "General Freight",
    "Palletised Goods",
    "Chilled / Temperature Controlled",
    "Hazardous Materials (ADR)",
    "Building Materials",
    "Automotive Parts",
    "Food & Beverage",
    "Retail / FMCG",
]


# ─── Database Setup ────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pods (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            delivery_date       DATE NOT NULL,
            delivery_time       TIME NOT NULL,

            -- DVLA fields
            vehicle_reg         TEXT NOT NULL,
            vehicle_type        TEXT,
            trailer_number      TEXT,
            driver_name         TEXT NOT NULL,
            carrier             TEXT DEFAULT 'Lionrock Haulage Limited',

            -- HMRC / freight fields
            cmr_number          TEXT,
            consignment_ref     TEXT NOT NULL,
            seal_number         TEXT,
            collection_address  TEXT NOT NULL,
            delivery_address    TEXT NOT NULL,
            goods_description   TEXT NOT NULL,
            weight_kg           REAL,
            quantity            TEXT,
            customer_name       TEXT NOT NULL,
            customer_company    TEXT,

            -- Signatures (base64 PNG)
            driver_signature    TEXT NOT NULL,
            recipient_signature TEXT NOT NULL,

            -- Supporting document (base64 image, optional)
            supporting_doc_image TEXT,

            -- Status
            status              TEXT DEFAULT 'delivered',
            notes               TEXT
        )
    """)
    # Migrate existing databases: add new columns if they don't exist
    existing = [r[1] for r in conn.execute("PRAGMA table_info(pods)").fetchall()]
    if "supporting_doc_image" not in existing:
        conn.execute("ALTER TABLE pods ADD COLUMN supporting_doc_image TEXT")
    if "trailer_number" not in existing:
        conn.execute("ALTER TABLE pods ADD COLUMN trailer_number TEXT")
    if "seal_number" not in existing:
        conn.execute("ALTER TABLE pods ADD COLUMN seal_number TEXT")
    if "carrier" not in existing:
        conn.execute("ALTER TABLE pods ADD COLUMN carrier TEXT DEFAULT 'Lionrock Haulage Limited'")
    # driver_licence may exist in old schema - leave it if present, just don't require it
    conn.commit()
    conn.close()


# ─── Routes ────────────────────────────────────────────────────────────────────

@app.route("/api/config")
def api_config():
    """Return driver list, vehicle map, address presets and goods presets for the form."""
    return jsonify({
        "vehicles": VEHICLES,
        "collection_addresses": COLLECTION_ADDRESSES,
        "goods_presets": GOODS_PRESETS,
    })


@app.route("/api/autocomplete/delivery")
def autocomplete_delivery():
    """Return distinct delivery addresses from past PODs for autocomplete."""
    q = request.args.get("q", "").strip()
    conn = get_db()
    if q:
        rows = conn.execute(
            "SELECT DISTINCT delivery_address FROM pods "
            "WHERE delivery_address LIKE ? ORDER BY delivery_address LIMIT 10",
            (f"%{q}%",)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT delivery_address FROM pods "
            "ORDER BY id DESC LIMIT 20"
        ).fetchall()
    conn.close()
    return jsonify([r["delivery_address"] for r in rows if r["delivery_address"]])


@app.route("/")
def index():
    return render_template("form.html", now=datetime.now())


@app.route("/submit", methods=["POST"])
def submit():
    data = request.get_json()
    required = [
        "delivery_date", "delivery_time", "vehicle_reg", "driver_name",
        "consignment_ref", "collection_address",
        "delivery_address", "goods_description", "customer_name",
        "driver_signature", "recipient_signature"
    ]
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"Missing field: {field}"}), 400

    conn = get_db()
    conn.execute("""
        INSERT INTO pods (
            delivery_date, delivery_time, vehicle_reg, vehicle_type,
            trailer_number, driver_name, carrier,
            cmr_number, consignment_ref, seal_number,
            collection_address, delivery_address, goods_description,
            weight_kg, quantity, customer_name, customer_company,
            driver_signature, recipient_signature,
            supporting_doc_image, status, notes
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        data["delivery_date"], data["delivery_time"],
        data["vehicle_reg"].upper(), data.get("vehicle_type", ""),
        data.get("trailer_number", "").upper(), data["driver_name"],
        data.get("carrier", "Lionrock Haulage Limited"),
        data.get("cmr_number", ""), data["consignment_ref"],
        data.get("seal_number", ""),
        data["collection_address"], data["delivery_address"],
        data["goods_description"], data.get("weight_kg"),
        data.get("quantity", ""), data["customer_name"],
        data.get("customer_company", ""),
        data["driver_signature"], data["recipient_signature"],
        data.get("supporting_doc_image"),
        data.get("status", "delivered"), data.get("notes", "")
    ))
    conn.commit()
    pod_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return jsonify({"success": True, "id": pod_id})


@app.route("/dashboard")
def dashboard():
    date_filter = request.args.get("date", "")
    conn = get_db()
    if date_filter:
        rows = conn.execute(
            "SELECT * FROM pods WHERE delivery_date = ? ORDER BY delivery_time DESC",
            (date_filter,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM pods ORDER BY delivery_date DESC, delivery_time DESC"
        ).fetchall()

    # Group by date
    grouped = {}
    for row in rows:
        d = row["delivery_date"]
        if d not in grouped:
            grouped[d] = []
        grouped[d].append(dict(row))

    conn.close()
    return render_template("dashboard.html", grouped=grouped, date_filter=date_filter)


@app.route("/pod/<int:pod_id>")
def view_pod(pod_id):
    conn = get_db()
    pod = conn.execute("SELECT * FROM pods WHERE id = ?", (pod_id,)).fetchone()
    conn.close()
    if not pod:
        return "POD not found", 404
    return render_template("pod_detail.html", pod=dict(pod))


@app.route("/api/pods")
def api_pods():
    date_filter = request.args.get("date", "")
    conn = get_db()
    if date_filter:
        rows = conn.execute(
            "SELECT id, delivery_date, delivery_time, vehicle_reg, driver_name, "
            "consignment_ref, customer_name, customer_company, status "
            "FROM pods WHERE delivery_date = ? ORDER BY delivery_time DESC",
            (date_filter,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, delivery_date, delivery_time, vehicle_reg, driver_name, "
            "consignment_ref, customer_name, customer_company, status "
            "FROM pods ORDER BY delivery_date DESC, delivery_time DESC"
        ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/pod/<int:pod_id>")
def api_pod_detail(pod_id):
    conn = get_db()
    pod = conn.execute("SELECT * FROM pods WHERE id = ?", (pod_id,)).fetchone()
    conn.close()
    if not pod:
        return jsonify({"error": "Not found"}), 404
    return jsonify(dict(pod))


# ─── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print("\n  ePOD System running at http://localhost:8080")
    print("  Dashboard:  http://localhost:8080/dashboard\n")
    app.run(debug=True, port=8080)
