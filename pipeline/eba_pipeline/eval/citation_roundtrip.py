import sqlite3
import sys


def run_citation_roundtrip(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    chunks = conn.execute(
        "SELECT c.chunk_id, d.eba_id, c.paragraph_ref, c.language, c.section_path, c.page_start "
        "FROM chunks c "
        "JOIN document_versions dv ON c.document_version_id = dv.version_id "
        "JOIN documents d ON dv.document_id = d.eba_id "
    ).fetchall()

    total = len(chunks)
    passed = 0
    failed = []

    for chunk in chunks:
        if chunk["paragraph_ref"]:
            result = conn.execute(
                "SELECT c.chunk_id FROM chunks c "
                "JOIN document_versions dv ON c.document_version_id = dv.version_id "
                "JOIN documents d ON dv.document_id = d.eba_id "
                "WHERE d.eba_id = ? AND c.paragraph_ref = ? AND c.language = ? AND c.chunk_id = ?",
                (chunk["eba_id"], chunk["paragraph_ref"], chunk["language"], chunk["chunk_id"])
            ).fetchone()
        else:
            result = conn.execute(
                "SELECT c.chunk_id FROM chunks c "
                "JOIN document_versions dv ON c.document_version_id = dv.version_id "
                "JOIN documents d ON dv.document_id = d.eba_id "
                "WHERE d.eba_id = ? AND c.section_path = ? AND c.page_start = ? AND c.language = ? AND c.chunk_id = ?",
                (chunk["eba_id"], chunk["section_path"], chunk["page_start"], chunk["language"], chunk["chunk_id"])
            ).fetchone()

        if result:
            passed += 1
        else:
            failed.append({
                "chunk_id": chunk["chunk_id"],
                "eba_id": chunk["eba_id"],
                "paragraph_ref": chunk["paragraph_ref"],
                "reason": "exact_chunk_not_found"
            })

    conn.close()
    pass_rate = passed / total if total > 0 else 0.0
    return {
        "total": total,
        "passed": passed,
        "failed_count": len(failed),
        "pass_rate": round(pass_rate, 4),
        "failures": failed[:20]
    }


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/eba.db"
    result = run_citation_roundtrip(db_path)
    print(f"Citation round-trip: {result['passed']}/{result['total']} passed ({result['pass_rate']*100:.1f}%)")
    if result["failures"]:
        print(f"Sample failures: {result['failures'][:3]}")
    sys.exit(0 if result["pass_rate"] >= 0.95 else 1)
