"""
Script à lancer UNE SEULE FOIS sur PythonAnywhere (ou en local) pour ajouter
50 étudiants fictifs de test à la base de données.

Utilisation :
    python3 seed_50_students.py
"""
import sqlite3
import os
from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), "unilu.db")

STUDENTS = [
    ("UNILU-CRIM-2026-100", "Trésor Katanga", "L2", 150, "etudiant123"),
    ("UNILU-CRIM-2026-101", "Fiston Kalenga", "L3", 150, "etudiant123"),
    ("UNILU-CRIM-2026-102", "Franck Ilunga", "L3", 150, "etudiant123"),
    ("UNILU-CRIM-2026-103", "Aline Mwamba", "L1", 150, "etudiant123"),
    ("UNILU-CRIM-2026-104", "Fiston Muteba", "L3", 150, "etudiant123"),
    ("UNILU-CRIM-2026-105", "Franck Kasongo", "L3", 150, "etudiant123"),
    ("UNILU-CRIM-2026-106", "Chantal Lukusa", "L3", 150, "etudiant123"),
    ("UNILU-CRIM-2026-107", "Grace Mwepu", "L1", 150, "etudiant123"),
    ("UNILU-CRIM-2026-108", "Prisca Mukendi", "L1", 150, "etudiant123"),
    ("UNILU-CRIM-2026-109", "Freddy Mutombo", "L1", 150, "etudiant123"),
    ("UNILU-CRIM-2026-110", "Merveille Ntumba", "L2", 150, "etudiant123"),
    ("UNILU-CRIM-2026-111", "Aline Katanga", "L2", 150, "etudiant123"),
    ("UNILU-CRIM-2026-112", "Junior Ilunga", "L3", 150, "etudiant123"),
    ("UNILU-CRIM-2026-113", "Sifa Ntumba", "L3", 150, "etudiant123"),
    ("UNILU-CRIM-2026-114", "Divin Kabongo", "L3", 150, "etudiant123"),
    ("UNILU-CRIM-2026-115", "Christian Ilunga", "L1", 150, "etudiant123"),
    ("UNILU-CRIM-2026-116", "Junior Mukendi", "L2", 150, "etudiant123"),
    ("UNILU-CRIM-2026-117", "Naomie Ntumba", "L2", 150, "etudiant123"),
    ("UNILU-CRIM-2026-118", "Bienvenu Tshibangu", "L3", 150, "etudiant123"),
    ("UNILU-CRIM-2026-119", "Rodrigue Nkulu", "L1", 150, "etudiant123"),
    ("UNILU-CRIM-2026-120", "Jean-Paul Lukusa", "L2", 150, "etudiant123"),
    ("UNILU-CRIM-2026-121", "Judith Kabeya", "L3", 150, "etudiant123"),
    ("UNILU-CRIM-2026-122", "Aline Kabeya", "L1", 150, "etudiant123"),
    ("UNILU-CRIM-2026-123", "Sarah Mukendi", "L1", 150, "etudiant123"),
    ("UNILU-CRIM-2026-124", "Josué Tshibangu", "L2", 150, "etudiant123"),
    ("UNILU-CRIM-2026-125", "Guy Mbuyi", "L3", 150, "etudiant123"),
    ("UNILU-CRIM-2026-126", "Divine Mukendi", "L1", 150, "etudiant123"),
    ("UNILU-CRIM-2026-127", "Franck Kabwe", "L2", 150, "etudiant123"),
    ("UNILU-CRIM-2026-128", "Espérance Mbuyi", "L2", 150, "etudiant123"),
    ("UNILU-CRIM-2026-129", "Patrick Muteba", "L2", 150, "etudiant123"),
    ("UNILU-CRIM-2026-130", "Grâce Mutombo", "L1", 150, "etudiant123"),
    ("UNILU-CRIM-2026-131", "Blaise Mbayo", "L1", 150, "etudiant123"),
    ("UNILU-CRIM-2026-132", "Sarah Mbayo", "L2", 150, "etudiant123"),
    ("UNILU-CRIM-2026-133", "Judith Mwamba", "L3", 150, "etudiant123"),
    ("UNILU-CRIM-2026-134", "Franck Mwepu", "L2", 150, "etudiant123"),
    ("UNILU-CRIM-2026-135", "Merveille Ngoy", "L2", 150, "etudiant123"),
    ("UNILU-CRIM-2026-136", "Dieudonné Mwamba", "L3", 150, "etudiant123"),
    ("UNILU-CRIM-2026-137", "Vanessa Mwepu", "L1", 150, "etudiant123"),
    ("UNILU-CRIM-2026-138", "Christian Nkulu", "L3", 150, "etudiant123"),
    ("UNILU-CRIM-2026-139", "Patrick Ntumba", "L1", 150, "etudiant123"),
    ("UNILU-CRIM-2026-140", "Rodrigue Kayembe", "L2", 150, "etudiant123"),
    ("UNILU-CRIM-2026-141", "Emmanuel Ntumba", "L2", 150, "etudiant123"),
    ("UNILU-CRIM-2026-142", "Grâce Kabeya", "L3", 150, "etudiant123"),
    ("UNILU-CRIM-2026-143", "Divin Katanga", "L2", 150, "etudiant123"),
    ("UNILU-CRIM-2026-144", "Franck Mwepu", "L1", 150, "etudiant123"),
    ("UNILU-CRIM-2026-145", "Guy Kabwe", "L1", 150, "etudiant123"),
    ("UNILU-CRIM-2026-146", "Vanessa Mbayo", "L2", 150, "etudiant123"),
    ("UNILU-CRIM-2026-147", "Franck Mwepu", "L3", 150, "etudiant123"),
    ("UNILU-CRIM-2026-148", "Christian Mbuyi", "L3", 150, "etudiant123"),
    ("UNILU-CRIM-2026-149", "Nadège Muteba", "L2", 150, "etudiant123"),
]

def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    added = 0
    skipped = 0

    for matricule, name, level, amount_due, password in STUDENTS:
        existing = cur.execute(
            "SELECT id FROM students WHERE UPPER(matricule) = UPPER(?)", (matricule,)
        ).fetchone()
        if existing:
            skipped += 1
            continue

        cur.execute("""
            INSERT INTO students (matricule, password_hash, name, faculty, level, amount_due, paid)
            VALUES (?, ?, ?, ?, ?, ?, 0)
        """, (
            matricule,
            generate_password_hash(password),
            name,
            "Faculté de Criminologie",
            level,
            amount_due,
        ))
        added += 1

    conn.commit()
    conn.close()
    print(f"Terminé : {added} étudiants ajoutés, {skipped} déjà existants (ignorés).")


if __name__ == "__main__":
    main()