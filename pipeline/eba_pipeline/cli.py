# pyright: reportUnknownVariableType=false, reportAny=false, reportUnknownMemberType=false, reportImplicitStringConcatenation=false, reportUnusedCallResult=false
import json
import subprocess
from pathlib import Path
from typing import cast

import click


def _coerce_expected_min_results(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    return 0


@click.group()
def cli():
    """EBA Pipeline CLI for processing EBA regulatory documents."""
    pass


@cli.command()
@click.option("--manifest", required=True)
@click.option("--output", required=True)
@click.option("--continue-on-error", is_flag=True, default=False)
def download(manifest: str, output: str, continue_on_error: bool) -> None:
    """Download seed documents from EBA."""
    from eba_pipeline.crawler.downloader import download_documents
    from eba_pipeline.crawler.manifest import build_manifest

    documents = cast(list[dict[str, object]], download_documents(manifest, output, continue_on_error))
    _ = build_manifest(output, documents)


@cli.command("discover")
@click.option("--output", required=True, help="Seed YAML manifest path to write")
@click.option("--limit", default=400, show_default=True, type=int)
@click.option("--pages-per-type", default=20, show_default=True, type=int)
@click.option("--sleep", "sleep_seconds", default=0.5, show_default=True, type=float)
@click.option(
    "--profile",
    default="current-applicable",
    show_default=True,
    type=click.Choice(["current-applicable", "broad"]),
    help="Discovery profile: production current/applicable corpus or broad stress-test/archive corpus.",
)
def discover(output: str, limit: int, pages_per_type: int, sleep_seconds: float, profile: str) -> None:
    """Discover official EBA PDF publications and write a seed manifest."""
    from collections import Counter

    from eba_pipeline.crawler.discovery import discover_publication_pdfs, write_seed_manifest

    documents = discover_publication_pdfs(
        limit=limit,
        pages_per_type=pages_per_type,
        sleep_seconds=sleep_seconds,
        profile=profile,
    )
    write_seed_manifest(documents, Path(output))
    counts = Counter(doc.document_type for doc in documents)
    click.echo(f"Discovered {len(documents)} official EBA PDFs using profile={profile} -> {output}")
    for document_type, count in sorted(counts.items()):
        click.echo(f"  {document_type}: {count}")


@cli.command("enrich")
@click.option("--manifest", required=True, help="Normalized manifest YAML")
@click.option("--pdfs", required=True, help="Downloaded PDFs directory")
@click.option("--output", required=True, help="Enriched manifest YAML with application_date")
@click.option("--relationships-out", default=None, help="Output relationships YAML")
def enrich(manifest: str, pdfs: str, output: str, relationships_out: str | None) -> None:
    """Extract application dates and relationships from PDF content."""
    from eba_pipeline.crawler.enrich import enrich_manifest

    enrichments = enrich_manifest(
        manifest_path=Path(manifest),
        pdfs_dir=Path(pdfs),
        output_path=Path(output),
    )
    with_date = sum(1 for e in enrichments if e.application_date)
    with_rels = sum(1 for e in enrichments if e.relationships)
    total_rels = sum(len(e.relationships) for e in enrichments)
    click.echo(f"Enriched {len(enrichments)} documents: {with_date} with application_date, {with_rels} with relationships ({total_rels} total).")

    if relationships_out:
        import yaml as _yaml

        all_rels = [r for e in enrichments for r in e.relationships]
        Path(relationships_out).parent.mkdir(parents=True, exist_ok=True)
        Path(relationships_out).write_text(
            _yaml.safe_dump({"relationships": all_rels}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        click.echo(f"Relationships written to {relationships_out}")


@cli.command("normalize")
@click.option("--manifest", required=True, help="Input seed YAML manifest")
@click.option("--pdfs", required=True, help="Downloaded PDFs directory")
@click.option("--output", required=True, help="Output normalized manifest YAML")
@click.option("--review-queue", default=None, help="Output review queue YAML for unresolved IDs")
def normalize(manifest: str, pdfs: str, output: str, review_queue: str | None) -> None:
    """Normalize synthetic eba_ids using official references from PDF content."""
    from eba_pipeline.crawler.normalize import normalize_manifest

    docs, results = normalize_manifest(
        manifest_path=Path(manifest),
        pdfs_dir=Path(pdfs),
        output_path=Path(output),
        review_queue_path=Path(review_queue) if review_queue else None,
    )
    high = sum(1 for r in results if r.confidence == "high")
    medium = sum(1 for r in results if r.confidence == "medium")
    unresolved = sum(1 for r in results if r.confidence == "unresolved")
    click.echo(f"Normalized {len(docs)} documents: {high} high, {medium} medium, {unresolved} unresolved.")
    if review_queue:
        click.echo(f"Review queue written to {review_queue}")


@cli.command()
@click.option("--input", "input_dir", required=True)
@click.option("--output", "output_dir", required=True)
@click.option("--manifest", default=None, help="Seed YAML used to map processed slugs to EBA IDs")
def parse(input_dir: str, output_dir: str, manifest: str | None) -> None:
    """Parse downloaded PDFs and paragraphize them."""
    from eba_pipeline.parser.paragraphize import paragraphize_all
    from eba_pipeline.parser.pdf_extract import extract_all_documents

    output_path = Path(output_dir)
    extract_all_documents(Path(input_dir), output_path)
    paragraphize_all(output_path, Path(manifest) if manifest else None)
    click.echo("Parse complete.")


@cli.command()
@click.option("--input", "input_dir", required=True)
@click.option("--manifest", default=None, help="Seed YAML used to map processed slugs to EBA IDs")
def paragraphize(input_dir: str, manifest: str | None) -> None:
    """Build chunks.json from extracted pages.json files."""
    from eba_pipeline.parser.paragraphize import paragraphize_all

    paragraphize_all(Path(input_dir), Path(manifest) if manifest else None)
    click.echo("Paragraphize complete.")


@cli.command()
@click.option("--input", "input_dir", required=True)
@click.option("--reports", default=None, help="Directory for quality report JSON files")
def quality(input_dir: str, reports: str | None) -> None:
    """Run quality gates on processed documents."""
    from eba_pipeline.config import QUALITY_REPORTS_DIR
    from eba_pipeline.parser.quality import run_quality_all

    results = run_quality_all(Path(input_dir), Path(reports) if reports else QUALITY_REPORTS_DIR)
    passed = sum(1 for result in results if result["passed"])
    click.echo(f"Quality complete: {passed}/{len(results)} passed.")


@cli.command("build-index")
@click.option("--output", required=True)
@click.option("--seed", default=None, help="Path to seed_documents.yaml (auto-detected if omitted)")
@click.option("--override", default=None, help="Path to relationships_override.yaml")
@click.option("--processed", default=None, help="Processed directory (defaults to data/processed)")
@click.option("--quality-reports", default=None, help="Quality reports directory")
@click.option("--embed", is_flag=True, default=False, help="Generate and store embeddings in chunks_vec")
@click.option("--model", default="nomic-embed-text", show_default=True, help="Ollama embedding model")
@click.option("--ollama-url", default="http://localhost:11434", show_default=True, help="Ollama server URL")
@click.option("--batch-size", default=32, show_default=True, type=int, help="Embedding batch size")
def build_index(output: str, seed: str | None, override: str | None, processed: str | None, quality_reports: str | None, embed: bool, model: str, ollama_url: str, batch_size: int) -> None:
    """Build SQLite/FTS5 index from processed data."""
    import sqlite3

    from eba_pipeline.config import PROCESSED_DIR, QUALITY_REPORTS_DIR
    from eba_pipeline.index.build_index import build_index as _build_index
    from eba_pipeline.relationships.extractor import extract_relationships

    _build_index(
        Path(output),
        Path(processed) if processed else PROCESSED_DIR,
        Path(quality_reports) if quality_reports else QUALITY_REPORTS_DIR,
        Path(seed) if seed else None,
        embed=embed,
        model=model,
        ollama_url=ollama_url,
        batch_size=batch_size,
    )

    seed_path = seed or str(Path(__file__).parent.parent / "seed_documents.yaml")
    override_path = override or str(Path(__file__).parent.parent / "relationships_override.yaml")
    relationships = extract_relationships(seed_path, override_path)

    enriched_rels_path = Path(seed_path).parent / "current-relationships.yaml" if seed else None
    if enriched_rels_path and not enriched_rels_path.exists():
        enriched_rels_path = Path(__file__).parent.parent.parent / "data" / "current-relationships.yaml"
    if enriched_rels_path and enriched_rels_path.exists():
        import yaml as _yaml
        enriched_data = _yaml.safe_load(enriched_rels_path.read_text()) or {}
        for rel in enriched_data.get("relationships", []):
            relationships.append({
                "source_eba_id": rel["source_eba_id"],
                "target_eba_id": rel["target_eba_id"],
                "relationship_type": rel["relationship_type"],
            })

    conn = sqlite3.connect(output)
    _ = conn.execute("DELETE FROM document_relationships")
    _ = conn.executemany(
        "INSERT INTO document_relationships (source_eba_id, target_eba_id, relationship_type) VALUES (?, ?, ?)",
        [(r["source_eba_id"], r["target_eba_id"], r["relationship_type"]) for r in relationships],
    )
    conn.commit()
    conn.close()

    click.echo(f"Relationships: inserted {len(relationships)} rows.")
    click.echo("Build-index complete.")


@cli.command()
@click.option("--db", required=True)
@click.option("--queries", default=None)
@click.option("--mode", default="queries", type=click.Choice(["queries", "citation-roundtrip"]))
@click.option("--tags", default=None, help="Comma-separated tags to filter queries (e.g. 'semantic,aml_cft')")
def eval(db: str, queries: str | None, mode: str, tags: str | None) -> None:
    """Run evaluation suite."""
    db_path = str(Path(db).resolve())

    if mode == "citation-roundtrip":
        import sys

        from eba_pipeline.eval.citation_roundtrip import run_citation_roundtrip

        result = run_citation_roundtrip(db_path)
        click.echo(
            f"Citation round-trip: {result['passed']}/{result['total']} passed ({result['pass_rate']*100:.1f}%)"
        )
        if result["failures"]:
            click.echo(f"Failures ({result['failed_count']}):")
            for failure in result["failures"]:
                click.echo(
                    f"  chunk_id={failure['chunk_id']} eba_id={failure['eba_id']} "
                    f"paragraph_ref={failure['paragraph_ref']} reason={failure['reason']}"
                )
        else:
            click.echo("No failures.")
        if result["pass_rate"] < 0.95:
            click.echo("FAIL: pass rate below 0.95 threshold", err=True)
            sys.exit(1)
        return

    if not queries:
        click.echo("--queries required for queries mode", err=True)
        return

    import yaml

    loaded = cast(dict[str, object], yaml.safe_load(Path(queries).read_text()) or {})
    fixtures = cast(list[dict[str, object]], loaded.get("queries", []))

    tag_filter: set[str] | None = None
    if tags:
        tag_filter = {t.strip() for t in tags.split(",") if t.strip()}

    if tag_filter:
        fixtures = [
            f for f in fixtures
            if tag_filter.intersection(set(cast(list[str], f.get("tags", []))))
        ]
        click.echo(f"Loaded {len(fixtures)} query fixtures (filtered by tags: {sorted(tag_filter)}).")
    else:
        click.echo(f"Loaded {len(fixtures)} query fixtures.")

    passed = 0
    failed = 0
    for fixture in fixtures:
        tool_name = str(fixture.get("tool", "eba_search"))
        query_text = str(fixture.get("query", ""))
        expected = str(fixture.get("expected_answerability", "partial"))
        expected_min_results = _coerce_expected_min_results(
            fixture.get("expected_min_results", 1 if expected != "no_match" else 0)
        )
        expected_eba_id = fixture.get("expected_eba_id")
        filters = cast(dict[str, object], fixture.get("filters", {}))
        label = str(fixture.get("id", query_text))
        try:
            if tool_name == "eba_search":
                arguments: dict[str, object] = {
                    "query": query_text,
                    "filters": filters,
                    "limit": max(expected_min_results, 10),
                }
            else:
                arguments = cast(dict[str, object], fixture.get("args", {}))
            request = {
                "jsonrpc": "2.0",
                "id": fixture.get("id", query_text),
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments,
                },
            }
            completed = subprocess.run(
                ["node", "dist/index.js", "--db", db_path],
                input=json.dumps(request) + "\n",
                cwd=Path(__file__).resolve().parents[2],
                text=True,
                capture_output=True,
                check=False,
                timeout=15,
            )
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip() or f"node exited {completed.returncode}")
            outer = json.loads(completed.stdout)
            payload = json.loads(outer["result"]["content"][0]["text"])
            got_answerability = str(payload.get("answerability"))
            reasons: list[str] = []
            if tool_name == "eba_search":
                citations = cast(list[dict[str, object]], payload.get("citations", []))
                if got_answerability != expected:
                    reasons.append(f"expected answerability {expected}, got {got_answerability}")
                if len(citations) < expected_min_results:
                    reasons.append(f"expected at least {expected_min_results} results, got {len(citations)}")
                if expected_eba_id and citations and not any(
                    citation.get("eba_id") == expected_eba_id for citation in citations
                ):
                    reasons.append(f"expected eba_id {expected_eba_id} not in results")
                if expected == "no_match" and citations:
                    reasons.append("expected zero citations")
                for citation in citations:
                    missing = [
                        field
                        for field in ("citation_id", "eba_id", "text", "citation", "page_start")
                        if field not in citation
                    ]
                    if missing:
                        reasons.append(f"missing citation fields {missing}")
                        break
        except Exception as error:
            reasons = [str(error)]

        if not reasons:
            passed += 1
        else:
            failed += 1
            click.echo(f"  FAIL: {label[:60]} → {'; '.join(reasons)}")

    total = passed + failed
    click.echo(f"Eval results: {passed}/{total} passed ({failed} failed)")
    if failed:
        raise SystemExit(1)
