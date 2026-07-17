"""
Source-inference demo — the hero-pillar evidence table, reproducible on
your own Coyote graph with one command.

WHAT THIS SHOWS (and what it doesn't)
-------------------------------------
Coyote keeps two role-separated corpora: what you READ (Webpage nodes,
content_role "input") and what you WROTE (Annotation nodes, content_role
"output"), linked by ground-truth provenance edges (HAS_ANNOTATION). This
script uses that record to print, for a note you wrote:

  1. THE NOTE      — your own prose, from the record.
  2. KNOWN SOURCE  — the page the note was written on. Coyote KNOWS this
                     from the annotation link; no similarity math involved.
  3. DIVERGENCE    — how far your prose sits from that source in embedding
                     space (1 - cosine). The number is the deliverable:
                     Coyote furnishes the evidence, you infer the meaning.
  4. NEAREST OTHER INPUTS — the pages your prose moved toward.
  5. VALIDATION    — the blind test: hide the link; can the prose ALONE
                     find its source among every page you read? (On the
                     reference corpus: median rank 2 of 56 for substantive
                     notes; pointer-notes fail by construction — their
                     meaning lives in the highlighted quote, and their
                     provenance is the annotation link itself.)

None of this is retrieval-as-product. The ranking math is deliberately
ordinary; what conventional IR lacks is the record it runs on — separated
input/output corpora, provenance ground truth, and a stable embedder that
makes the numbers comparable over time.

Honest limits: the input record is your BROWSING trace only (no books,
conversations, podcasts), so divergence is divergence from the recorded
trace. Everything here is deterministic — no LLM computes or interprets
the record.

USAGE (from the host, stack running)
------------------------------------
  docker exec coyote-coyote_app-1 python /app/coyote/demos/source_inference.py
      List your annotations (index, date, prose preview).
  docker exec coyote-coyote_app-1 python /app/coyote/demos/source_inference.py --note "span embeddings"
      Evidence table for every note whose prose contains the substring.
  docker exec coyote-coyote_app-1 python /app/coyote/demos/source_inference.py --index 3
  docker exec coyote-coyote_app-1 python /app/coyote/demos/source_inference.py --all
  Options: --top K (default 5 neighbours), --json (machine-readable).

The prose is embedded AD HOC at query time (prose alone — never the
highlighted quote, never the stored digest), so the demo makes no writes
and does not depend on the stored annotation embedding's composition.
"""

import argparse
import json
import logging
import math
import sys
from pathlib import Path

# Pointer-note boundary used by the ground-truth instrument: notes with
# less prose than this carry their meaning in the quote, not the prose.
PROSE_MIN_CHARS = 15


# ── pure functions (stdlib only; unit-tested host-side) ──────────────────

def cosine(a, b):
    """Cosine similarity of two equal-length vectors; 0.0 if either is degenerate."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def dedupe_pages_by_url(pages):
    """
    One entry per URL, keeping the most recent visit. Revisit nodes carry
    near-identical embeddings; without dedupe they distort every rank.
    `pages` = iterable of dicts with url/title/timestamp/embedding.
    """
    best = {}
    for p in pages:
        url = p.get("url")
        if not url or not p.get("embedding"):
            continue
        ts = p.get("timestamp") or ""
        if url not in best or ts > (best[url].get("timestamp") or ""):
            best[url] = p
    return sorted(best.values(), key=lambda p: p["url"])


def rank_pages(query_vec, pages):
    """All pages ranked by cosine to the query vector, deterministic tie-break."""
    scored = [(cosine(query_vec, p["embedding"]), p) for p in pages]
    scored.sort(key=lambda sp: (-sp[0], sp[1]["url"]))
    return scored


def blind_rank_of_source(ranked, source_url):
    """1-based rank of the known source in a ranked list, or None if absent."""
    for i, (_, page) in enumerate(ranked, start=1):
        if page["url"] == source_url:
            return i
    return None


def is_pointer_note(prose):
    """True when the prose is too thin to carry meaning on its own."""
    return len((prose or "").strip()) < PROSE_MIN_CHARS


def resolve_source_embedding(source_url, source_embedding, pages):
    """
    The HAS_ANNOTATION edge can sit on an unembedded duplicate of the source
    page (Hypothesis-created twin — see the duplicate-Webpage Known Issue in
    CLAUDE.md) while an embedded copy of the same URL exists in the corpus.
    Webpage identity is the URL, not the node: fall back to the corpus copy
    so divergence reflects the page, not which twin holds the edge.
    """
    if source_embedding:
        return source_embedding
    for p in pages:
        if p.get("url") == source_url:
            return p.get("embedding")
    return None


# ── graph access (container-only imports kept out of module top-level) ───

def _connect():
    # Driver-level notification chatter (missing-property warnings on young
    # graphs) would drown the evidence table.
    logging.getLogger("neo4j").setLevel(logging.ERROR)
    app_root = Path(__file__).resolve().parents[2]  # /app
    sys.path.insert(0, str(app_root))
    from coyote.utils.config_manager import connect_to_neo4j
    return connect_to_neo4j()


def fetch_annotations(driver):
    cypher = """
    MATCH (w:Webpage)-[:HAS_ANNOTATION]->(a:Annotation)
    RETURN a.annotation_id AS annotation_id,
           a.annotation_text AS prose,
           a.highlighted_text AS quote,
           a.timestamp AS timestamp,
           w.url AS source_url,
           coalesce(w.title, a.webpage_title) AS source_title,
           w.embedding AS source_embedding
    ORDER BY a.timestamp, a.annotation_id
    """
    with driver.session() as session:
        return [dict(r) for r in session.run(cypher)]


def fetch_input_pages(driver):
    cypher = """
    MATCH (w:Webpage)
    WHERE w.embedding IS NOT NULL AND w.content_role = 'input'
    RETURN w.url AS url, w.title AS title,
           w.timestamp AS timestamp, w.embedding AS embedding
    """
    with driver.session() as session:
        return [dict(r) for r in session.run(cypher)]


def embed_prose(prose):
    from coyote.coyote_embedder import embed_text
    return embed_text(prose)


# ── evidence table ────────────────────────────────────────────────────────

def build_evidence(annotation, pages, top_k):
    """
    Assemble the evidence dict for one annotation against deduped input
    pages. Returns a plain dict (json-serializable except embeddings,
    which are never included).
    """
    prose = (annotation.get("prose") or "").strip()
    result = {
        "annotation_id": annotation.get("annotation_id"),
        "timestamp": annotation.get("timestamp"),
        "prose": prose,
        "known_source": {
            "url": annotation.get("source_url"),
            "title": annotation.get("source_title"),
        },
        "pointer_note": is_pointer_note(prose),
        "corpus_size": len(pages),
    }
    if result["pointer_note"]:
        result["note"] = (
            "Pointer-note: the prose is too thin to measure on its own; its "
            "meaning lives in the highlighted quote and its provenance IS "
            "the annotation link shown above. Prose-alone ranking is not "
            "meaningful for this class (fails by construction)."
        )
        return result

    query_vec = embed_prose(prose)
    if query_vec is None:
        result["note"] = "Embedding failed for this prose; nothing to measure."
        return result

    ranked = rank_pages(query_vec, pages)
    source_url = annotation.get("source_url")
    source_emb = resolve_source_embedding(
        source_url, annotation.get("source_embedding"), pages
    )

    if source_emb:
        result["divergence_from_source"] = round(1.0 - cosine(query_vec, source_emb), 4)
    else:
        result["note"] = (
            "The source page has no stored embedding (e.g. it entered the "
            "graph via Hypothesis only), so divergence is unavailable; the "
            "provenance link above still holds."
        )

    result["nearest_inputs"] = [
        {
            "rank": i,
            "cosine": round(score, 4),
            "divergence": round(1.0 - score, 4),
            "is_known_source": page["url"] == source_url,
            "title": page.get("title"),
            "url": page["url"],
        }
        for i, (score, page) in enumerate(ranked[:top_k], start=1)
    ]
    result["blind_source_rank"] = blind_rank_of_source(ranked, source_url)
    return result


def render_evidence(ev):
    lines = []
    w = lines.append
    w("=" * 72)
    w(f"NOTE ({ev.get('timestamp') or 'no timestamp'})")
    w(f'  "{ev["prose"]}"' if ev["prose"] else "  (no prose — highlight only)")
    w("")
    w("KNOWN SOURCE (from the record — the annotation link, no similarity math)")
    src = ev["known_source"]
    w(f"  {src.get('title') or '(untitled)'}")
    w(f"  {src.get('url')}")
    if ev.get("pointer_note") or "nearest_inputs" not in ev:
        w("")
        w(f"  {ev.get('note', '')}")
        w("=" * 72)
        return "\n".join(lines)
    w("")
    if "divergence_from_source" in ev:
        w(f"DIVERGENCE from source: {ev['divergence_from_source']}")
        w("  (1 - cosine of your prose vs the source page. The number is the")
        w("   evidence; what it means about your thinking is yours to infer.)")
    else:
        w(f"DIVERGENCE from source: unavailable — {ev.get('note', '')}")
    w("")
    w(f"NEAREST INPUTS (of {ev['corpus_size']} pages you read):")
    for n in ev["nearest_inputs"]:
        marker = "  <-- the known source" if n["is_known_source"] else ""
        w(f"  {n['rank']:>2}. cos {n['cosine']:.3f}  {n.get('title') or n['url']}{marker}")
    w("")
    rank = ev.get("blind_source_rank")
    w("VALIDATION — the blind test: hide the link; can the prose ALONE find")
    if rank is None:
        w("  its source?  Source not in the embedded corpus — untestable here.")
    else:
        w(f"  its source?  Recovered at rank {rank} of {ev['corpus_size']}.")
    w("=" * 72)
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Print the source-inference evidence table for your annotations."
    )
    parser.add_argument("--note", help="run for notes whose prose contains this substring")
    parser.add_argument("--index", type=int, help="run for the Nth note from the listing")
    parser.add_argument("--all", action="store_true", help="run for every note")
    parser.add_argument("--top", type=int, default=5, help="neighbours to show (default 5)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    driver = _connect()
    try:
        annotations = fetch_annotations(driver)
        pages = dedupe_pages_by_url(fetch_input_pages(driver))
    finally:
        driver.close()

    if not annotations:
        print("No annotations in the graph yet. Annotate something you read, "
              "let the pipeline process it, then re-run.")
        return 1

    if args.note:
        needle = args.note.lower()
        selected = [a for a in annotations if needle in (a.get("prose") or "").lower()]
    elif args.index is not None:
        selected = [annotations[args.index - 1]] if 1 <= args.index <= len(annotations) else []
    elif args.all:
        selected = annotations
    else:
        print(f"{len(annotations)} annotations on record "
              f"({len(pages)} embedded input pages). Pick one:\n")
        for i, a in enumerate(annotations, start=1):
            prose = (a.get("prose") or "").strip().replace("\n", " ")
            preview = prose[:70] + ("..." if len(prose) > 70 else "") if prose else "(highlight only)"
            tag = " [pointer]" if is_pointer_note(prose) else ""
            print(f"  {i:>3}. {(a.get('timestamp') or '')[:10]}  {preview}{tag}")
        print("\nRun again with --index N, --note SUBSTRING, or --all.")
        return 0

    if not selected:
        print("No matching annotation.")
        return 1

    evidences = [build_evidence(a, pages, args.top) for a in selected]
    if args.json:
        print(json.dumps(evidences, indent=2))
    else:
        for ev in evidences:
            print(render_evidence(ev))
    return 0


if __name__ == "__main__":
    sys.exit(main())
