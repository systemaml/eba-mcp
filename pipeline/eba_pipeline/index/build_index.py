import hashlib
import json
from pathlib import Path
import sqlite3
from typing import cast

from eba_pipeline.index.sqlite_store import (
    get_connection,
    init_schema,
    init_vec_schema,
    insert_chunk,
    insert_chunk_vectors,
    insert_document,
    insert_document_version,
    update_corpus_manifest,
)
from eba_pipeline.index.fts import populate_fts
from eba_pipeline.index.split_chunks import split_mega_chunks
from eba_pipeline.parser.quality import validate_unique_chunk_ids

ChunkRecord = dict[str, object]
SeedDoc = dict[str, object]

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def build_index(
    output_db: Path,
    processed_dir: Path,
    quality_reports_dir: Path,
    seed_yaml_path: Path | None = None,
    embed: bool = False,
    model: str = "nomic-embed-text",
    ollama_url: str = "http://localhost:11434",
    batch_size: int = 32,
    resume: bool = False,
) -> None:
    import yaml

    if resume and output_db.exists():
        if not embed:
            raise ValueError("--resume requires --embed (nothing to resume without embedding step)")
        print(f"  Resume mode: reusing existing DB {output_db}")
        conn = get_connection(output_db)
        _populate_vectors(conn, model=model, ollama_url=ollama_url, batch_size=batch_size, resume=True)
        manifest_hash = hashlib.sha256(
            "|".join(sorted(
                str(row[0]) for row in conn.execute("SELECT chunk_id FROM chunks").fetchall()
            )).encode()
        ).hexdigest()
        doc_count = cast(int, conn.execute("SELECT count(*) FROM documents").fetchone()[0])
        actual_chunks = cast(int, conn.execute("SELECT count(*) FROM chunks").fetchone()[0])
        from eba_pipeline.index.embeddings import NOMIC_EMBED_TEXT_DIM, _expected_embedding_dim
        embedding_dim = _expected_embedding_dim(model) or NOMIC_EMBED_TEXT_DIM
        update_corpus_manifest(conn, manifest_hash, doc_count, actual_chunks, embedding_model=model, embedding_dim=embedding_dim)
        print(f"  Manifest: {doc_count} docs, {actual_chunks} chunks, embedding_model={model}, embedding_dim={embedding_dim}, hash={manifest_hash[:16]}...")
        conn.close()
        return

    if output_db.exists():
        output_db.unlink()
    output_db.parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection(output_db)
    init_schema(conn, SCHEMA_PATH)

    seed_yaml = seed_yaml_path or Path(__file__).parent.parent.parent.parent / "pipeline" / "seed_documents.yaml"
    seed_docs: dict[str, SeedDoc] = {}
    if seed_yaml.exists():
        loaded = cast(dict[str, object], yaml.safe_load(seed_yaml.read_text()) or {})
        for doc in cast(list[SeedDoc], loaded.get("documents", [])):
            slug = str(doc["eba_id"]).replace("/", "-").replace(" ", "-")
            seed_docs[slug] = doc
            original_slug = str(doc.get("_original_slug", ""))
            if original_slug:
                seed_docs[original_slug] = doc

    total_chunks = 0
    all_chunk_ids: list[str] = []

    for doc_dir in sorted(processed_dir.iterdir()):
        if not doc_dir.is_dir():
            continue
        chunks_file = doc_dir / "chunks.json"
        quality_file = quality_reports_dir / f"{doc_dir.name}.json"

        if not chunks_file.exists():
            continue

        if quality_file.exists():
            quality = cast(dict[str, object], json.loads(quality_file.read_text()))
            if not quality.get("passed", False):
                print(f"  [skip] {doc_dir.name} (quality gate failed)")
                continue

        chunks = cast(list[ChunkRecord], json.loads(chunks_file.read_text()))
        if not chunks:
            continue

        chunks = cast(list[ChunkRecord], split_mega_chunks(list(chunks)))
        validate_unique_chunk_ids(chunks, f"{doc_dir.name} ({chunks_file})")

        seed_meta = seed_docs.get(doc_dir.name, {})
        eba_id = str(seed_meta.get("eba_id") or chunks[0].get("eba_id") or doc_dir.name)

        doc_row = {
            "eba_id": eba_id,
            "title": str(seed_meta.get("title", eba_id)),
            "document_type": str(seed_meta.get("document_type", "guidelines")),
            "topic": str(seed_meta.get("topic", "AML/CFT")),
            "language": str(seed_meta.get("language", "en")),
            "publication_url": str(seed_meta.get("publication_url", "")),
            "published_at": str(seed_meta.get("published_at", "")),
            "application_date": str(seed_meta.get("application_date", "")),
            "applicability_status": str(seed_meta.get("applicability_status", "applicable")),
            "publication_status": str(seed_meta.get("publication_status", "final")),
            "is_canonical": 1 if seed_meta.get("is_canonical", True) else 0,
        }
        _ = insert_document(conn, doc_row)

        version_row = {
            "document_id": eba_id,
            "version_label": "1.0",
            "published_at": str(seed_meta.get("published_at", "")),
            "file_sha256": str(seed_meta.get("file_sha256", "")),
            "file_path": str(doc_dir),
            "is_current": 1,
        }
        version_id = insert_document_version(conn, version_row)

        for chunk in chunks:
            chunk_row = {
                "chunk_id": chunk["chunk_id"],
                "document_version_id": version_id,
                "language": chunk.get("language", "en"),
                "section_path": chunk.get("section_path", ""),
                "paragraph_ref": chunk.get("paragraph_ref"),
                "page_start": chunk.get("page_start"),
                "page_end": chunk.get("page_end"),
                "text": chunk["text"],
                "text_hash": chunk["text_hash"],
                "chunk_type": chunk.get("chunk_type", "paragraph"),
                "sequence_no": chunk.get("sequence_no", 0),
            }
            try:
                insert_chunk(conn, chunk_row)
            except sqlite3.IntegrityError as error:
                message = (
                    f"Failed to insert chunk_id {chunk['chunk_id']} for {doc_dir.name}; duplicate "
                    "chunk_id should have been caught before DB insertion"
                )
                raise ValueError(
                    message
                ) from error
            all_chunk_ids.append(str(chunk["chunk_id"]))

        conn.commit()
        total_chunks += len(chunks)
        print(f"  Indexed {doc_dir.name}: {len(chunks)} chunks")

    fts_count = populate_fts(conn)
    print(f"  FTS populated: {fts_count} entries")

    if embed:
        _populate_vectors(conn, model=model, ollama_url=ollama_url, batch_size=batch_size, resume=False)

    manifest_hash = hashlib.sha256("|".join(sorted(all_chunk_ids)).encode()).hexdigest()
    doc_count = cast(int, conn.execute("SELECT count(*) FROM documents").fetchone()[0])
    actual_chunks = cast(int, conn.execute("SELECT count(*) FROM chunks").fetchone()[0])

    if embed:
        from eba_pipeline.index.embeddings import NOMIC_EMBED_TEXT_DIM, _expected_embedding_dim
        embedding_dim = _expected_embedding_dim(model) or NOMIC_EMBED_TEXT_DIM
        update_corpus_manifest(conn, manifest_hash, doc_count, actual_chunks, embedding_model=model, embedding_dim=embedding_dim)
        print(f"  Manifest: {doc_count} docs, {actual_chunks} chunks, embedding_model={model}, embedding_dim={embedding_dim}, hash={manifest_hash[:16]}...")
    else:
        update_corpus_manifest(conn, manifest_hash, doc_count, actual_chunks)
        print(f"  Manifest: {doc_count} docs, {actual_chunks} chunks, hash={manifest_hash[:16]}...")

    conn.close()


def _populate_vectors(
    conn: sqlite3.Connection,
    model: str,
    ollama_url: str,
    batch_size: int,
    resume: bool = False,
) -> None:
    from eba_pipeline.index.embeddings import NOMIC_EMBED_TEXT_DIM, _expected_embedding_dim, generate_embeddings

    embedding_dim = _expected_embedding_dim(model) or NOMIC_EMBED_TEXT_DIM
    init_vec_schema(conn, embedding_dim)

    if resume:
        rows = conn.execute(
            "SELECT c.rowid, c.chunk_id, c.text FROM chunks c"
            " WHERE c.rowid NOT IN (SELECT rowid FROM chunks_vec)"
            " ORDER BY c.rowid"
        ).fetchall()
        already_done = cast(int, conn.execute("SELECT count(*) FROM chunks_vec").fetchone()[0])
        total_in_db = cast(int, conn.execute("SELECT count(*) FROM chunks").fetchone()[0])
        if not rows:
            print(f"  Resume: all {total_in_db} chunks already embedded, nothing to do.")
            return
        print(f"  Resume: {already_done}/{total_in_db} already embedded, continuing with {len(rows)} remaining...")
    else:
        rows = conn.execute("SELECT rowid, chunk_id, text FROM chunks ORDER BY rowid").fetchall()

    chunk_records = [{"chunk_id": row[1], "text": row[2]} for row in rows]
    rowids = [row[0] for row in rows]

    try:
        print(f"  Generating embeddings for {len(chunk_records)} chunks with model={model}...")
        vectors = generate_embeddings(chunk_records, model=model, ollama_url=ollama_url, batch_size=batch_size)

        if len(vectors) != len(rowids):
            raise RuntimeError(f"Vector count mismatch: {len(vectors)} vectors for {len(rowids)} chunks")

        insert_chunk_vectors(conn, list(zip(rowids, vectors)))
    except Exception:
        if not resume:
            conn.execute("DROP TABLE IF EXISTS chunks_vec")
            conn.commit()
        raise

    vec_count = cast(int, conn.execute("SELECT count(*) FROM chunks_vec").fetchone()[0])
    print(f"  chunks_vec populated: {vec_count} vectors")
