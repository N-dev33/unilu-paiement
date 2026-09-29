# api_mobile.py
#
# Blueprint JSON pour l'appli mobile React Native.
# Réutilise la même base SQLite et la même logique métier que app.py (web).

import hmac
import hashlib
import base64
import json
import sqlite3
from datetime import datetime
from functools import wraps

from flask import Blueprint, request, jsonify

from app import (
    app,
    get_db_connection,
    fetch_students_with_balance,
    sign_receipt,
    verify_receipt_signature,
    FEE_ITEMS,
    TOTAL_DUE_CDF,
    EXCHANGE_RATE_CDF_PER_USD,
    PROMOTIONS,
)
from werkzeug.security import check_password_hash

api_bp = Blueprint("api_mobile", __name__, url_prefix="/api")

TOKEN_SIGNING_KEY = app.secret_key.encode()


# ---------------------------------------------------------------------------
# Auth par token signé (équivalent mobile des sessions cookie du site web)
# ---------------------------------------------------------------------------

def make_auth_token(role, user_id):
    payload = {"role": role, "uid": user_id, "iat": datetime.now().isoformat()}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    b64 = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(TOKEN_SIGNING_KEY, b64.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{b64}.{sig}"


def read_auth_token(token):
    try:
        b64, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(TOKEN_SIGNING_KEY, b64.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        padding = "=" * (-len(b64) % 4)
        return json.loads(base64.urlsafe_b64decode(b64 + padding))
    except Exception:
        return None


def get_bearer_token():
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:]
    return None


def student_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        payload = read_auth_token(get_bearer_token() or "")
        if not payload or payload.get("role") != "student":
            return jsonify({"error": "unauthorized"}), 401
        request.student_id = payload["uid"]
        return f(*args, **kwargs)
    return wrapper


def agent_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        payload = read_auth_token(get_bearer_token() or "")
        if not payload or payload.get("role") != "agent":
            return jsonify({"error": "unauthorized"}), 401
        request.staff_id = payload["uid"]
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Sérialisation
# ---------------------------------------------------------------------------

def student_to_dict(row, paid_cdf=None):
    d = dict(row)
    d.pop("password_hash", None)
    if paid_cdf is not None:
        d["paid_cdf"] = paid_cdf
        d["remaining_cdf"] = TOTAL_DUE_CDF - paid_cdf
    return d


def payment_to_dict(row):
    d = dict(row)
    return d


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@api_bp.route("/auth/student/login", methods=["POST"])
def api_student_login():
    data = request.get_json(force=True, silent=True) or {}
    matricule = (data.get("matricule") or "").strip()
    password = data.get("password") or ""

    conn = get_db_connection()
    student = conn.execute(
        "SELECT * FROM students WHERE matricule = ?", (matricule,)
    ).fetchone()
    conn.close()

    if not student or not check_password_hash(student["password_hash"], password):
        return jsonify({"error": "Matricule ou mot de passe incorrect"}), 401

    token = make_auth_token("student", student["id"])
    return jsonify({"token": token, "student": student_to_dict(student)})


@api_bp.route("/auth/agent/login", methods=["POST"])
def api_agent_login():
    data = request.get_json(force=True, silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    conn = get_db_connection()
    staff = conn.execute(
        "SELECT * FROM staff WHERE username = ?", (username,)
    ).fetchone()
    conn.close()

    if not staff or not check_password_hash(staff["password_hash"], password):
        return jsonify({"error": "Identifiants incorrects"}), 401

    token = make_auth_token("agent", staff["id"])
    return jsonify({"token": token, "staff": {"id": staff["id"], "name": staff["name"], "username": staff["username"]}})


# ---------------------------------------------------------------------------
# Étudiant : profil, frais, paiement, reçu, réclamations
# ---------------------------------------------------------------------------

@api_bp.route("/fees", methods=["GET"])
def api_fees():
    return jsonify({
        "items": [{"code": c, "label": l, "amount_cdf": a} for c, l, a in FEE_ITEMS],
        "total_due_cdf": TOTAL_DUE_CDF,
        "exchange_rate_cdf_per_usd": EXCHANGE_RATE_CDF_PER_USD,
    })


@api_bp.route("/student/summary", methods=["GET"])
@student_required
def api_student_summary():
    conn = get_db_connection()
    student = conn.execute("SELECT * FROM students WHERE id = ?", (request.student_id,)).fetchone()
    if not student:
        conn.close()
        return jsonify({"error": "not_found"}), 404

    payments = conn.execute(
        "SELECT * FROM payments WHERE student_id = ? ORDER BY id ASC", (request.student_id,)
    ).fetchall()
    conn.close()

    paid_codes = {p["item_code"] for p in payments}
    paid_cdf = sum(p["amount_cdf"] for p in payments)

    next_item = None
    for code, label, amount in FEE_ITEMS:
        if code not in paid_codes:
            next_item = {"code": code, "label": label, "amount_cdf": amount}
            break

    return jsonify({
        "student": student_to_dict(student, paid_cdf=paid_cdf),
        "payments": [payment_to_dict(p) for p in payments],
        "next_item": next_item,
    })


@api_bp.route("/pay", methods=["POST"])
@student_required
def api_pay():
    data = request.get_json(force=True, silent=True) or {}
    item_code = data.get("item_code", "")
    currency = data.get("currency", "CDF")
    method = data.get("method", "mobile_money")

    valid_codes = {code for code, _, _ in FEE_ITEMS}
    if item_code not in valid_codes:
        return jsonify({"error": "item_code invalide"}), 400
    if currency not in ("CDF", "USD"):
        return jsonify({"error": "devise invalide"}), 400

    conn = get_db_connection()
    student = conn.execute("SELECT * FROM students WHERE id = ?", (request.student_id,)).fetchone()
    if not student:
        conn.close()
        return jsonify({"error": "not_found"}), 404

    paid_codes = {
        row["item_code"]
        for row in conn.execute(
            "SELECT item_code FROM payments WHERE student_id = ?", (request.student_id,)
        ).fetchall()
    }
    next_expected = None
    for code, _, _ in FEE_ITEMS:
        if code not in paid_codes:
            next_expected = code
            break

    if item_code != next_expected:
        conn.close()
        return jsonify({"error": "Cet élément n'est pas le prochain attendu dans l'ordre de paiement"}), 400

    item_label = dict((c, l) for c, l, _ in FEE_ITEMS)[item_code]
    amount_cdf = dict((c, a) for c, _, a in FEE_ITEMS)[item_code]
    amount_paid = round(amount_cdf / EXCHANGE_RATE_CDF_PER_USD, 2) if currency == "USD" else amount_cdf
    paid_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        cur = conn.execute("""
            INSERT INTO payments (student_id, item_code, item_label, amount_cdf, currency, amount_paid, method, paid_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (request.student_id, item_code, item_label, amount_cdf, currency, amount_paid, method, paid_at))
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Cet élément a déjà été payé"}), 409

    payment_id = cur.lastrowid
    token = sign_receipt({
        "pid": payment_id, "sid": request.student_id, "code": item_code,
        "amt": amount_cdf, "at": paid_at,
    })
    conn.execute("UPDATE payments SET qr_token = ? WHERE id = ?", (token, payment_id))
    conn.commit()

    payment = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    conn.close()

    # Le client mobile génère lui-même le QR visuel à partir de `qr_token`.
    return jsonify({"payment": payment_to_dict(payment)}), 201


@api_bp.route("/receipt/<int:payment_id>", methods=["GET"])
def api_receipt(payment_id):
    payload_student = read_auth_token(get_bearer_token() or "")
    if not payload_student:
        return jsonify({"error": "unauthorized"}), 401

    conn = get_db_connection()
    payment = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if not payment:
        conn.close()
        return jsonify({"error": "not_found"}), 404

    is_owner = payload_student.get("role") == "student" and payload_student.get("uid") == payment["student_id"]
    is_agent = payload_student.get("role") == "agent"
    if not is_owner and not is_agent:
        conn.close()
        return jsonify({"error": "unauthorized"}), 401

    student = conn.execute("SELECT * FROM students WHERE id = ?", (payment["student_id"],)).fetchone()
    paid_total_cdf = conn.execute(
        "SELECT COALESCE(SUM(amount_cdf), 0) FROM payments WHERE student_id = ?", (student["id"],)
    ).fetchone()[0]
    conn.close()

    return jsonify({
        "payment": payment_to_dict(payment),
        "student": student_to_dict(student),
        "remaining_cdf": TOTAL_DUE_CDF - paid_total_cdf,
    })


@api_bp.route("/claims", methods=["GET", "POST"])
def api_claims():
    if request.method == "POST":
        payload = read_auth_token(get_bearer_token() or "")
        if not payload or payload.get("role") != "student":
            return jsonify({"error": "unauthorized"}), 401
        data = request.get_json(force=True, silent=True) or {}
        item_code = data.get("item_code", "")
        reference = data.get("reference", "")
        message = data.get("message", "")

        item_label = dict((c, l) for c, l, _ in FEE_ITEMS).get(item_code)
        if not item_label:
            return jsonify({"error": "item_code invalide"}), 400

        conn = get_db_connection()
        conn.execute("""
            INSERT INTO claims (student_id, item_code, item_label, reference, message, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """, (payload["uid"], item_code, item_label, reference, message, datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.commit()
        conn.close()
        return jsonify({"ok": True}), 201

    # GET -> réservé agent
    payload = read_auth_token(get_bearer_token() or "")
    if not payload or payload.get("role") != "agent":
        return jsonify({"error": "unauthorized"}), 401
    status = request.args.get("status", "pending")
    conn = get_db_connection()
    rows = conn.execute(
        """SELECT c.*, s.name AS student_name, s.matricule
           FROM claims c JOIN students s ON s.id = c.student_id
           WHERE c.status = ? ORDER BY c.id DESC""", (status,)
    ).fetchall()
    conn.close()
    return jsonify({"claims": [dict(r) for r in rows]})


@api_bp.route("/agent/claims/<int:claim_id>/<string:action>", methods=["POST"])
@agent_required
def api_claim_action(claim_id, action):
    if action not in ("approve", "reject"):
        return jsonify({"error": "action invalide"}), 400
    data = request.get_json(force=True, silent=True) or {}
    note = data.get("note", "")

    conn = get_db_connection()
    staff = conn.execute("SELECT name FROM staff WHERE id = ?", (request.staff_id,)).fetchone()
    conn.execute(
        "UPDATE claims SET status = ?, reviewed_by = ?, reviewed_at = ?, review_note = ? WHERE id = ?",
        ("approved" if action == "approve" else "rejected", staff["name"] if staff else "Agent",
         datetime.now().strftime("%Y-%m-%d %H:%M"), note, claim_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Agent : dashboard, vérification QR
# ---------------------------------------------------------------------------

@api_bp.route("/agent/dashboard", methods=["GET"])
@agent_required
def api_agent_dashboard():
    promotion = request.args.get("promotion", "").upper()
    query = request.args.get("q", "").strip()

    conn = get_db_connection()
    if promotion and promotion in PROMOTIONS:
        students = fetch_students_with_balance(conn, "WHERE s.level = ?", (promotion,))
    elif query:
        like = f"%{query}%"
        students = fetch_students_with_balance(conn, "WHERE s.name LIKE ? OR s.matricule LIKE ?", (like, like))
    else:
        students = fetch_students_with_balance(conn)

    total = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    pending_claims = conn.execute("SELECT COUNT(*) FROM claims WHERE status = 'pending'").fetchone()[0]
    conn.close()

    students = [dict(s) for s in students]
    paid_count = sum(1 for s in students if s["status"] == "À jour")
    return jsonify({
        "students": [student_to_dict(s, paid_cdf=s["paid_cdf"]) for s in students],
        "total": total,
        "paid_count": paid_count,
        "pending_claims_count": pending_claims,
        "promotions": PROMOTIONS,
    })


@api_bp.route("/agent/verify", methods=["POST"])
@agent_required
def api_agent_verify():
    data = request.get_json(force=True, silent=True) or {}
    token = data.get("token", "")

    payload = verify_receipt_signature(token)
    if payload is None:
        return jsonify({"status": "invalid"}), 200

    conn = get_db_connection()
    payment = conn.execute("SELECT * FROM payments WHERE qr_token = ?", (token,)).fetchone()
    if not payment:
        conn.close()
        return jsonify({"status": "invalid"}), 200

    student = conn.execute("SELECT * FROM students WHERE id = ?", (payment["student_id"],)).fetchone()

    if payment["scanned_at"]:
        conn.close()
        return jsonify({
            "status": "already_used",
            "payment": payment_to_dict(payment),
            "student": student_to_dict(student),
        })

    scanned_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    staff = conn.execute("SELECT name FROM staff WHERE id = ?", (request.staff_id,)).fetchone()
    scanned_by = staff["name"] if staff else "Agent inconnu"

    conn.execute(
        "UPDATE payments SET scanned_at = ?, scanned_by = ? WHERE id = ?",
        (scanned_at, scanned_by, payment["id"]),
    )
    conn.commit()

    paid_total_cdf = conn.execute(
        "SELECT COALESCE(SUM(amount_cdf), 0) FROM payments WHERE student_id = ?", (student["id"],)
    ).fetchone()[0]
    conn.close()

    return jsonify({
        "status": "valid",
        "payment": payment_to_dict(payment),
        "student": student_to_dict(student),
        "scanned_at": scanned_at,
        "remaining_cdf": TOTAL_DUE_CDF - paid_total_cdf,
    })