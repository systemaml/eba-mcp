import sqlite3


def populate_fts(conn: sqlite3.Connection) -> int:
    conn.execute("DELETE FROM chunks_fts")
    conn.execute(
        """
        INSERT INTO chunks_fts (rowid, chunk_id, eba_id, title, section_path, paragraph_ref, body, topic, document_type)
        SELECT c.rowid, c.chunk_id, d.eba_id, d.title, c.section_path, c.paragraph_ref, c.text, d.topic, d.document_type
        FROM chunks c
        JOIN document_versions dv ON c.document_version_id = dv.version_id
        JOIN documents d ON dv.document_id = d.eba_id
        """
    )
    conn.commit()
    count: int = conn.execute("SELECT count(*) FROM chunks_fts").fetchone()[0]
    return count


def search_fts(conn: sqlite3.Connection, query: str, limit: int = 10) -> list:
    safe_query = query.replace('"', '""').replace("'", "''")
    try:
        rows = conn.execute(
            """SELECT c.chunk_id, d.eba_id, c.paragraph_ref, c.section_path, c.text, c.page_start
               FROM chunks_fts f
               JOIN chunks c ON f.rowid = c.rowid
               JOIN document_versions dv ON c.document_version_id = dv.version_id
               JOIN documents d ON dv.document_id = d.eba_id
               WHERE chunks_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (safe_query, limit),
        ).fetchall()
    except sqlite3.OperationalError as error:
        raise RuntimeError(f"FTS query failed: {error}") from error
    return rows
