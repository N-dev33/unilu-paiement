from flask import Flask, render_template, request, redirect, url_for, abort, session
import sqlite3
import os
import secrets
from datetime import datetime
from functools import wraps
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import qrcode

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "cle-secrete-demo-a-remplacer-en-production")

DB_PATH = os.path.join(os.path.dirname(__file__), "unilu.db")
QR_FOLDER = os.path.join(os.path.dirname(__file__), "static", "qrcodes")
PHOTO_FOLDER = os.path.join(os.path.dirname(__file__), "static", "photos")
os.makedirs(QR_FOLDER, exist_ok=True)
os.makedirs(PHOTO_FOLDER, exist_ok=True)
ALLOWED_PHOTO_EXT = {"png", "jpg", "jpeg"}

# Taux de change indicatif — à ajuster périodiquement (marché parallèle/officiel fluctuant)
EXCHANGE_RATE_CDF_PER_USD = 2290

# Structure réelle des frais académiques UNILU : 3 tranches + frais connexes
FEE_ITEMS = [
    ("tranche1", "Tranche 1", 350000),
    ("tranche2", "Tranche 2", 350000),
    ("tranche3", "Tranche 3", 350000),
    ("connexes", "Frais connexes", 250000),
]
TOTAL_DUE_CDF = sum(amount for _, _, amount in FEE_ITEMS)  # 1 300 000 FC


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_students_with_balance(conn, where_sql="", params=()):
    """Récupère des étudiants avec leur montant payé (CDF) et solde restant calculés."""
    query = f"""
        SELECT s.*, COALESCE(SUM(p.amount_cdf), 0) AS paid_cdf
        FROM students s
        LEFT JOIN payments p ON p.student_id = s.id
        {where_sql}
        GROUP BY s.id
        ORDER BY paid_cdf ASC, s.name ASC
    """
    rows = conn.execute(query, params).fetchall()
    results = []
    for row in rows:
        d = dict(row)
        d["remaining_cdf"] = TOTAL_DUE_CDF - d["paid_cdf"]
        if d["paid_cdf"] == 0:
            d["status"] = "Non payé"
        elif d["remaining_cdf"] <= 0:
            d["status"] = "À jour"
        else:
            d["status"] = "Partiel"
        results.append(d)
    return results


def init_db():
    """Crée les tables et insère des comptes de démonstration si la base est vide."""
    conn = get_db_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            matricule TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            faculty TEXT NOT NULL,
            level TEXT NOT NULL,
            amount_due INTEGER NOT NULL,
            paid INTEGER NOT NULL DEFAULT 0,
            paid_at TEXT,
            payment_method TEXT,
            qr_token TEXT,
            scanned_at TEXT,
            photo_filename TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS staff (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            item_code TEXT NOT NULL,
            item_label TEXT NOT NULL,
            amount_cdf INTEGER NOT NULL,
            currency TEXT NOT NULL,
            amount_paid INTEGER NOT NULL,
            method TEXT,
            paid_at TEXT,
            qr_token TEXT UNIQUE,
            scanned_at TEXT,
            scanned_by TEXT,
            FOREIGN KEY (student_id) REFERENCES students (id)
        )
    """)
    conn.commit()

    # Migration : ajout de colonnes sur une base déjà existante (ignore si déjà présentes)
    for statement in [
        "ALTER TABLE students ADD COLUMN department TEXT",
        "ALTER TABLE students ADD COLUMN payment_currency TEXT",
        "ALTER TABLE students ADD COLUMN amount_local_paid INTEGER",
    ]:
        try:
            conn.execute(statement)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # la colonne existe déjà

    existing_students = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    if existing_students == 0:
        demo_password = generate_password_hash("etudiant123")
        demo_students = [
            ("UNILU-CRIM-2026-014", demo_password, "Kalenga Mwamba", "Faculté de Criminologie", "L2", 150),
            ("UNILU-CRIM-2026-027", demo_password, "Aline Kabongo", "Faculté de Criminologie", "L1", 150),
            ("UNILU-CRIM-2026-033", demo_password, "Trésor Ilunga", "Faculté de Criminologie", "L3", 150),
            ("UNILU-CRIM-2026-041", demo_password, "Grace Mutombo", "Faculté de Criminologie", "L2", 150),
        ]
        conn.executemany("""
            INSERT INTO students (matricule, password_hash, name, faculty, level, amount_due, paid)
            VALUES (?, ?, ?, ?, ?, ?, 0)
        """, demo_students)
        conn.commit()

    existing_staff = conn.execute("SELECT COUNT(*) FROM staff").fetchone()[0]
    if existing_staff == 0:
        conn.execute("""
            INSERT INTO staff (username, password_hash, name)
            VALUES (?, ?, ?)
        """, ("agent1", generate_password_hash("agent123"), "Agent Trésorerie - Fac. Criminologie"))
        conn.commit()

    conn.close()


def generate_qr(token, payment_id):
    """Génère l'image QR pointant vers la page de vérification de l'agent."""
    verify_url = url_for("verify", token=token, _external=True)
    img = qrcode.make(verify_url)
    filename = f"receipt_{payment_id}.png"
    img.save(os.path.join(QR_FOLDER, filename))
    return filename


def allowed_photo(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_PHOTO_EXT


def student_login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("student_id"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


def staff_login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("staff_id"):
            return redirect(url_for("agent_login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    """Connexion étudiant : matricule + mot de passe."""
    error = None
    if request.method == "POST":
        matricule = request.form.get("matricule", "").strip()
        password = request.form.get("password", "")

        conn = get_db_connection()
        student = conn.execute(
            "SELECT * FROM students WHERE UPPER(matricule) = UPPER(?)", (matricule,)
        ).fetchone()
        conn.close()

        if student and check_password_hash(student["password_hash"], password):
            session["student_id"] = student["id"]
            next_url = request.args.get("next") or url_for("pay_screen", student_id=student["id"])
            return redirect(next_url)
        error = "Matricule ou mot de passe incorrect."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.pop("student_id", None)
    return redirect(url_for("login"))


@app.route("/agent/login", methods=["GET", "POST"])
def agent_login():
    """Connexion agent/faculté : identifiant + mot de passe."""
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db_connection()
        staff = conn.execute("SELECT * FROM staff WHERE username = ?", (username,)).fetchone()
        conn.close()

        if staff and check_password_hash(staff["password_hash"], password):
            session["staff_id"] = staff["id"]
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)
        error = "Identifiant ou mot de passe incorrect."

    return render_template("agent_login.html", error=error)


@app.route("/agent/logout")
def agent_logout():
    session.pop("staff_id", None)
    return redirect(url_for("agent_login"))


@app.route("/")
def index():
    """Redirige vers l'espace approprié selon la connexion en cours."""
    if session.get("student_id"):
        return redirect(url_for("pay_screen", student_id=session["student_id"]))
    return redirect(url_for("login"))


@app.route("/pay-screen/<int:student_id>")
@student_login_required
def pay_screen(student_id):
    """Affiche l'écran de paiement — uniquement pour l'étudiant connecté lui-même."""
    if session["student_id"] != student_id:
        abort(403)
    conn = get_db_connection()
    student = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    if not student:
        conn.close()
        abort(404)

    paid_codes = {
        row["item_code"]
        for row in conn.execute(
            "SELECT item_code FROM payments WHERE student_id = ?", (student_id,)
        ).fetchall()
    }
    conn.close()

    items_status = []
    next_payable_code = None
    for code, label, amount in FEE_ITEMS:
        is_paid = code in paid_codes
        if not is_paid and next_payable_code is None:
            next_payable_code = code
        items_status.append({"code": code, "label": label, "amount": amount, "paid": is_paid})

    total_paid_cdf = sum(item["amount"] for item in items_status if item["paid"])
    remaining_cdf = TOTAL_DUE_CDF - total_paid_cdf

    return render_template(
        "index.html",
        student=student,
        items_status=items_status,
        next_payable_code=next_payable_code,
        total_due_cdf=TOTAL_DUE_CDF,
        total_paid_cdf=total_paid_cdf,
        remaining_cdf=remaining_cdf,
        exchange_rate=EXCHANGE_RATE_CDF_PER_USD,
    )


@app.route("/pay/<int:student_id>", methods=["POST"])
@student_login_required
def pay(student_id):
    """Enregistre le paiement d'UN élément de frais (tranche ou frais connexes), dans l'ordre imposé."""
    if session["student_id"] != student_id:
        abort(403)

    method = request.form.get("method", "mobile_money")
    currency = request.form.get("currency", "CDF")
    item_code = request.form.get("item_code", "")

    valid_codes = {code for code, _, _ in FEE_ITEMS}
    if item_code not in valid_codes:
        abort(400)

    conn = get_db_connection()
    student = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    if not student:
        conn.close()
        abort(404)

    paid_codes = {
        row["item_code"]
        for row in conn.execute(
            "SELECT item_code FROM payments WHERE student_id = ?", (student_id,)
        ).fetchall()
    }

    # Vérifie que c'est bien le prochain élément attendu dans l'ordre (tranche1 -> tranche2 -> tranche3 -> connexes)
    next_expected = None
    for code, _, _ in FEE_ITEMS:
        if code not in paid_codes:
            next_expected = code
            break

    if item_code != next_expected:
        conn.close()
        abort(400)

    item_label = dict((c, l) for c, l, _ in FEE_ITEMS)[item_code]
    amount_cdf = dict((c, a) for c, _, a in FEE_ITEMS)[item_code]

    if currency == "USD":
        amount_paid = round(amount_cdf / EXCHANGE_RATE_CDF_PER_USD, 2)
    else:
        amount_paid = amount_cdf

    token = secrets.token_urlsafe(24)
    paid_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    cur = conn.execute("""
        INSERT INTO payments (student_id, item_code, item_label, amount_cdf, currency, amount_paid, method, paid_at, qr_token)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (student_id, item_code, item_label, amount_cdf, currency, amount_paid, method, paid_at, token))
    conn.commit()
    payment_id = cur.lastrowid
    conn.close()

    generate_qr(token, payment_id)

    return redirect(url_for("receipt", payment_id=payment_id))


@app.route("/receipt/<int:payment_id>")
def receipt(payment_id):
    """
    Affiche le reçu numérique d'UN paiement précis (une tranche ou les frais connexes), avec son QR code.
    Accessible par l'étudiant concerné OU par un agent connecté.
    """
    conn = get_db_connection()
    payment = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    if not payment:
        conn.close()
        abort(404)

    student = conn.execute("SELECT * FROM students WHERE id = ?", (payment["student_id"],)).fetchone()

    if session.get("student_id") != student["id"] and not session.get("staff_id"):
        conn.close()
        return redirect(url_for("login", next=request.path))

    paid_total_cdf = conn.execute(
        "SELECT COALESCE(SUM(amount_cdf), 0) FROM payments WHERE student_id = ?", (student["id"],)
    ).fetchone()[0]
    conn.close()

    remaining_cdf = TOTAL_DUE_CDF - paid_total_cdf
    qr_filename = f"receipt_{payment_id}.png"

    return render_template(
        "receipt.html",
        payment=payment,
        student=student,
        qr_filename=qr_filename,
        remaining_cdf=remaining_cdf,
    )


@app.route("/students/<int:student_id>/payments")
@staff_login_required
def student_payments(student_id):
    """Historique des paiements d'un étudiant (vue agent), avec accès à chaque reçu."""
    conn = get_db_connection()
    student = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    if not student:
        conn.close()
        abort(404)
    payments = conn.execute(
        "SELECT * FROM payments WHERE student_id = ? ORDER BY id", (student_id,)
    ).fetchall()
    paid_total_cdf = sum(p["amount_cdf"] for p in payments)
    conn.close()
    remaining_cdf = TOTAL_DUE_CDF - paid_total_cdf

    return render_template(
        "student_payments.html",
        student=student,
        payments=payments,
        remaining_cdf=remaining_cdf,
        total_due_cdf=TOTAL_DUE_CDF,
    )


@app.route("/students/<int:student_id>/photo", methods=["GET", "POST"])
@staff_login_required
def upload_photo(student_id):
    """Formulaire d'upload de la photo d'un étudiant (utilisée pour la vérification anti-fraude)."""
    conn = get_db_connection()
    student = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    if not student:
        conn.close()
        abort(404)

    error = None
    if request.method == "POST":
        file = request.files.get("photo")
        if not file or file.filename == "":
            error = "Choisis une image avant de valider."
        elif not allowed_photo(file.filename):
            error = "Formats acceptés : JPG ou PNG uniquement."
        else:
            filename = secure_filename(f"student_{student_id}.{file.filename.rsplit('.', 1)[1].lower()}")
            file.save(os.path.join(PHOTO_FOLDER, filename))
            conn.execute("UPDATE students SET photo_filename = ? WHERE id = ?", (filename, student_id))
            conn.commit()
            conn.close()
            return redirect(url_for("dashboard"))

    conn.close()
    return render_template("upload_photo.html", student=student, error=error)


@app.route("/students/add", methods=["GET", "POST"])
@staff_login_required
def add_student():
    """Formulaire agent : créer un nouvel étudiant, avec mot de passe généré automatiquement."""
    error = None
    generated_password = None
    created_student = None

    if request.method == "POST":
        matricule = request.form.get("matricule", "").strip()
        name = request.form.get("name", "").strip()
        level = request.form.get("level", "").strip().upper()
        department = request.form.get("department", "").strip() or None

        if not matricule or not name or not level:
            error = "Le matricule, le nom et le niveau sont obligatoires."
        elif level not in PROMOTIONS:
            error = "Niveau invalide (L1, L2, L3, M1 ou M2)."
        elif level != "L1" and department not in ("CEE", "SI", "PE"):
            error = "Un département (CEE, SI ou Protection de l'enfant) est requis à partir de L2."
        else:
            conn = get_db_connection()
            existing = conn.execute(
                "SELECT id FROM students WHERE UPPER(matricule) = UPPER(?)", (matricule,)
            ).fetchone()

            if existing:
                error = "Ce matricule existe déjà."
                conn.close()
            else:
                generated_password = secrets.token_hex(3)  # ex: "a1b2c3", facile à communiquer
                conn.execute("""
                    INSERT INTO students (matricule, password_hash, name, faculty, level, department, amount_due, paid)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """, (
                    matricule,
                    generate_password_hash(generated_password),
                    name,
                    "Faculté de Criminologie",
                    level,
                    department,
                    TOTAL_DUE_CDF,
                ))
                conn.commit()
                created_student = matricule
                conn.close()

    return render_template(
        "add_student.html",
        error=error,
        generated_password=generated_password,
        created_student=created_student,
    )


@app.route("/dashboard")
@staff_login_required
def dashboard():
    """Tableau de bord de la faculté : vue d'ensemble de tous les étudiants et leur solde."""
    query = request.args.get("q", "").strip()
    conn = get_db_connection()
    if query:
        like = f"%{query}%"
        students = fetch_students_with_balance(
            conn, "WHERE s.name LIKE ? OR s.matricule LIKE ?", (like, like)
        )
    else:
        students = fetch_students_with_balance(conn)

    total = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    paid_count = sum(1 for s in students if s["status"] == "À jour")
    conn.close()

    return render_template(
        "dashboard.html",
        students=students,
        total=total,
        paid_count=paid_count,
        query=query,
    )


PROMOTIONS = ["L1", "L2", "L3", "M1", "M2"]
DEPARTMENT_ORDER = ["Général", "CEE", "SI", "PE"]


@app.route("/dashboard/<promotion>")
@staff_login_required
def dashboard_promotion(promotion):
    """Tableau de bord d'une promotion précise, avec une colonne par département si applicable."""
    promotion = promotion.upper()
    if promotion not in PROMOTIONS:
        abort(404)

    conn = get_db_connection()
    students = fetch_students_with_balance(conn, "WHERE s.level = ?", (promotion,))
    conn.close()

    groups = {}
    for s in students:
        dept = s["department"] or "Général"
        groups.setdefault(dept, []).append(s)

    ordered_groups = {d: groups[d] for d in DEPARTMENT_ORDER if d in groups}

    total = len(students)
    paid_count = sum(1 for s in students if s["status"] == "À jour")

    return render_template(
        "dashboard_promotion.html",
        promotion=promotion,
        promotions=PROMOTIONS,
        groups=ordered_groups,
        total=total,
        paid_count=paid_count,
    )


@app.route("/verify/<token>")
@staff_login_required
def verify(token):
    """
    Page côté agent : ce que le scan du QR code affiche.
    Montre l'identité de l'étudiant pour vérification visuelle,
    bloque toute réutilisation du même reçu (anti-fraude), et trace l'agent qui a scanné.
    """
    conn = get_db_connection()
    payment = conn.execute("SELECT * FROM payments WHERE qr_token = ?", (token,)).fetchone()

    if not payment:
        conn.close()
        return render_template("verify.html", status="invalid")

    student = conn.execute("SELECT * FROM students WHERE id = ?", (payment["student_id"],)).fetchone()

    if payment["scanned_at"]:
        conn.close()
        return render_template("verify.html", status="already_used", payment=payment, student=student)

    scanned_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    staff = conn.execute("SELECT name FROM staff WHERE id = ?", (session.get("staff_id"),)).fetchone()
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
    remaining_cdf = TOTAL_DUE_CDF - paid_total_cdf

    return render_template(
        "verify.html", status="valid", payment=payment, student=student,
        scanned_at=scanned_at, remaining_cdf=remaining_cdf,
    )


@app.route("/reset/<int:student_id>")
@staff_login_required
def reset(student_id):
    """Utilitaire de démo : supprime tous les paiements de l'étudiant pour retester le flux."""
    conn = get_db_connection()
    conn.execute("DELETE FROM payments WHERE student_id = ?", (student_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("dashboard"))


init_db()

if __name__ == "__main__":
    app.run(debug=True)