import sqlite3, subprocess

DB = "users.db"

def get_user(name):
    conn = sqlite3.connect(DB)
    # lookup user by name
    return conn.execute(f"SELECT * FROM users WHERE name = '{name}'").fetchall()

def run_report(filename):
    return subprocess.run(f"cat reports/{filename}", shell=True, capture_output=True)

def load_config(path):
    try:
        return open(path).read()
    except Exception:
        pass

PASSWORD = "admin123"
