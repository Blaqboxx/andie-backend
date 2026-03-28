import sqlite3

DB_PATH = "knowledge/dictionary.db"

def save_definition(word, definition):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT OR REPLACE INTO dictionary (word, definition)
        VALUES (?, ?)
    """, (word, definition))

    conn.commit()
    conn.close()


def lookup_definition(word):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT definition FROM dictionary WHERE word = ?
    """, (word,))

    result = cursor.fetchone()
    conn.close()

    if result:
        return result[0]
    return None
