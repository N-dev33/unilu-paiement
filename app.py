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


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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


def generate_qr(token, student_id):
    """Génère l'image QR pointant vers la page de vérification de l'agent."""
    verify_url = url_for("verify", token=token, _external=True)
    img = qrcode.make(verify_url)
    filename = f"receipt_{student_id}.png"
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
    conn.close()
    if not student:
        abort(404)
    return render_template("index.html", student=student, exchange_rate=EXCHANGE_RATE_CDF_PER_USD)


@app.route("/pay/<int:student_id>", methods=["POST"])
@student_login_required
def pay(student_id):
    """Simule le paiement : marque l'étudiant comme payé et génère son reçu QR."""
    if session["student_id"] != student_id:
        abort(403)

    method = request.form.get("method", "mobile_money")
    currency = request.form.get("currency", "USD")

    conn = get_db_connection()
    student = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    if not student:
        conn.close()
        abort(404)

    token = secrets.token_urlsafe(24)
    paid_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    amount_local_paid = None
    if currency == "CDF":
        amount_local_paid = round(student["amount_due"] * EXCHANGE_RATE_CDF_PER_USD)

    conn.execute("""
        UPDATE students
        SET paid = 1, paid_at = ?, payment_method = ?, qr_token = ?, scanned_at = NULL,
            payment_currency = ?, amount_local_paid = ?
        WHERE id = ?
    """, (paid_at, method, token, currency, amount_local_paid, student_id))
    conn.commit()
    conn.close()

    generate_qr(token, student_id)

    return redirect(url_for("receipt", student_id=student_id))


@app.route("/receipt/<int:student_id>")
def receipt(student_id):
    """
    Affiche le reçu numérique avec le QR code.
    Accessible par l'étudiant concerné OU par un agent connecté (consultation depuis le tableau de bord).
    """
    if session.get("student_id") != student_id and not session.get("staff_id"):
        return redirect(url_for("login", next=request.path))

    conn = get_db_connection()
    student = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    conn.close()
    if not student or not student["paid"]:
        abort(404)
    qr_filename = f"receipt_{student_id}.png"
    return render_template("receipt.html", student=student, qr_filename=qr_filename)


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
        level = request.form.get("level", "").strip()
        amount_due = request.form.get("amount_due", "").strip()

        if not matricule or not name or not level or not amount_due:
            error = "Tous les champs sont obligatoires."
        elif not amount_due.isdigit():
            error = "Le montant doit être un nombre entier."
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
                    INSERT INTO students (matricule, password_hash, name, faculty, level, amount_due, paid)
                    VALUES (?, ?, ?, ?, ?, ?, 0)
                """, (
                    matricule,
                    generate_password_hash(generated_password),
                    name,
                    "Faculté de Criminologie",
                    level,
                    int(amount_due),
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
    """Tableau de bord de la faculté : vue d'ensemble de tous les étudiants et leur statut."""
    query = request.args.get("q", "").strip()
    conn = get_db_connection()
    if query:
        like = f"%{query}%"
        students = conn.execute(
            "SELECT * FROM students WHERE name LIKE ? OR matricule LIKE ? ORDER BY paid ASC, name ASC",
            (like, like),
        ).fetchall()
    else:
        students = conn.execute("SELECT * FROM students ORDER BY paid ASC, name ASC").fetchall()

    total = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    paid_count = conn.execute("SELECT COUNT(*) FROM students WHERE paid = 1").fetchone()[0]
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
    students = conn.execute(
        "SELECT * FROM students WHERE level = ? ORDER BY department, name",
        (promotion,),
    ).fetchall()
    conn.close()

    groups = {}
    for s in students:
        dept = s["department"] or "Général"
        groups.setdefault(dept, []).append(s)

    ordered_groups = {d: groups[d] for d in DEPARTMENT_ORDER if d in groups}

    total = len(students)
    paid_count = sum(1 for s in students if s["paid"])

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
    et bloque toute réutilisation du même reçu (anti-fraude).
    """
    conn = get_db_connection()
    student = conn.execute("SELECT * FROM students WHERE qr_token = ?", (token,)).fetchone()

    if not student:
        conn.close()
        return render_template("verify.html", status="invalid")

    if student["scanned_at"]:
        conn.close()
        return render_template("verify.html", status="already_used", student=student)

    scanned_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn.execute("UPDATE students SET scanned_at = ? WHERE id = ?", (scanned_at, student["id"]))
    conn.commit()
    conn.close()

    return render_template("verify.html", status="valid", student=student, scanned_at=scanned_at)


@app.route("/reset/<int:student_id>")
@staff_login_required
def reset(student_id):
    """Utilitaire de démo : remet l'étudiant à l'état 'non payé' pour retester le flux."""
    conn = get_db_connection()
    conn.execute("""
        UPDATE students SET paid = 0, paid_at = NULL, payment_method = NULL,
        qr_token = NULL, scanned_at = NULL WHERE id = ?
    """, (student_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("dashboard"))


init_db()

if __name__ == "__main__":
    app.run(debug=True)