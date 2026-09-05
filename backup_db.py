"""
Script de sauvegarde automatique de la base de données.
À programmer comme tâche planifiée sur PythonAnywhere (onglet "Tasks")
pour qu'il tourne une fois par jour, par exemple à 2h du matin.

Conserve les sauvegardes des 14 derniers jours, supprime les plus anciennes.
"""
import shutil
import os
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "unilu.db")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
KEEP_DAYS = 14

os.makedirs(BACKUP_DIR, exist_ok=True)


def main():
    if not os.path.exists(DB_PATH):
        print("Aucune base de données trouvée — rien à sauvegarder.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    backup_path = os.path.join(BACKUP_DIR, f"unilu_{timestamp}.db")
    shutil.copy2(DB_PATH, backup_path)
    print(f"Sauvegarde créée : {backup_path}")

    # Nettoyage des sauvegardes trop anciennes
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    removed = 0
    for filename in os.listdir(BACKUP_DIR):
        if filename.startswith("unilu_") and filename.endswith(".db"):
            filepath = os.path.join(BACKUP_DIR, filename)
            mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
            if mtime < cutoff:
                os.remove(filepath)
                removed += 1
    if removed:
        print(f"{removed} ancienne(s) sauvegarde(s) supprimée(s) (plus de {KEEP_DAYS} jours).")


if __name__ == "__main__":
    main()