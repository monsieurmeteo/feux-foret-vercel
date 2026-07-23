import sqlite3

conn = sqlite3.connect("data/feux_historique.db")
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = cursor.fetchall()
print("Tables in database:", tables)
for t in tables:
    t_name = t[0]
    cursor.execute(f"SELECT COUNT(*) FROM {t_name};")
    print(f"Row count for {t_name}:", cursor.fetchone()[0])
conn.close()
