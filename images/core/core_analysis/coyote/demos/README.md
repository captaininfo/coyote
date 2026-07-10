# Coyote demos

Small, deterministic, read-only scripts that run measurements over your own
Coyote record. No LLM is involved anywhere in these — *LLMs verbalize; they
do not compute the record.* Coyote furnishes the evidence; you infer the
meaning.

## source_inference.py — the evidence table

For a note you wrote, print what the record knows and what it measures:

1. **The note** — your own prose.
2. **Known source** — the page you wrote it on. Coyote knows this from the
   annotation link itself; no similarity math involved.
3. **Divergence** — how far your prose sits from that source in embedding
   space (`1 - cosine`). The number is the deliverable.
4. **Nearest inputs** — the pages you read that your prose moved toward.
5. **Validation** — the blind test: hide the link; can your prose *alone*
   find its source among every page you read?

```bash
# stack running; from anywhere on the host
docker exec coyote-coyote_app-1 python /app/coyote/demos/source_inference.py            # list your notes
docker exec coyote-coyote_app-1 python /app/coyote/demos/source_inference.py --index 3  # evidence table
docker exec coyote-coyote_app-1 python /app/coyote/demos/source_inference.py --note "span embeddings"
docker exec coyote-coyote_app-1 python /app/coyote/demos/source_inference.py --all --top 8
docker exec coyote-coyote_app-1 python /app/coyote/demos/source_inference.py --all --json
```

### Why this isn't "just vector search"

You could rebuild this script's *math* on any vector database in a weekend —
the retrieval step is deliberately ordinary. What you couldn't rebuild is
what it runs *on*: a continuously kept record in which what you read and
what you wrote are separate, timestamped, provenance-linked corpora under a
stable embedder. Conventional IR answers "which documents match this
query?" — the score is discarded once ranked, and the system never knows
whether a match is *right*. Here the score **is** the measurement, and the
blind test is checkable because the record holds the true answer
independently of any similarity computation.

### Honest limits

- Notes are measured **prose-alone**, embedded ad hoc at query time — never
  the highlighted quote (that would smuggle the source's own words into the
  "output" and make recovery circular).
- **Pointer-notes** ("what does this mean?") are flagged, not ranked: their
  meaning lives in the quote and their provenance is the annotation link.
  Prose-alone ranking fails on them *by construction*.
- The input record is your **browsing trace only** — no books,
  conversations, or podcasts — so divergence means divergence from the
  *recorded* trace, not from everything that shaped you.
- Reference numbers (one user, one reading session, 56 candidate pages):
  substantive notes recovered their source at median rank 2, top-5 83% of
  the time. Your corpus will differ; that is rather the point — run it and
  see.

Promoting this evidence table into the Coyote UI is a scoped, self-contained
contribution — a good first issue if you want one.
