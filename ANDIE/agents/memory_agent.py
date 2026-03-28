import sqlite3

DB_PATH = "knowledge/dictionary.db"


def get_memory(term: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT definition FROM dictionary WHERE word=?", (term,))
    result = cursor.fetchone()

    conn.close()

    if result:
        return result[0]
    return None


def save_memory(term: str, value: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        "INSERT OR REPLACE INTO dictionary (word, definition) VALUES (?, ?)",
        (term, value),
    )

    conn.commit()
    conn.close()
