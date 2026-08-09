"""
Knowledge-shape demo — the coverage map, reproducible on your own Coyote
graph with one command.

WHAT THIS SHOWS (and what it doesn't)
-------------------------------------
Coyote resolves each thing you read to a WikiData concept and, from that
concept, walks UP the real-world ontology (subclass-of / instance-of) to
the broader categories it belongs to. Today that lineage is stored FLAT —
every page links directly to its concept AND to each ancestor, and the
concepts are NOT linked to each other (CLAUDE.md "ontology is flat, not a
DAG" Known Issue). So the graph alone can't tell you the SHAPE of what
you know; it's a fan of per-page tags.

But the arrangement isn't lost — it's cached locally. For every concept
you've touched, `wikidata_cache.db` holds that concept's parent links
(the P279/P31 hierarchy, built during the ancestor walk). This script
reads two LOCAL stores — Neo4j (which concepts your reading touches) and
that cache (how those concepts nest) — and reconstructs, in memory, the
slice of the ontology your reading actually covers. It then prints:

  1. COVERAGE SUMMARY  — how many concepts you've touched, across how many
                         broad areas (roots), how deep (specific) you go.
  2. CONCEPT HUBS      — the concepts the most of your reading orbits.
  3. KNOWLEDGE TREE    — the reconstructed hierarchy, indented, with a page
                         count on each concept you actually read about and
                         the connecting ancestors dimmed.
  4. FRONTIER          — specific concepts you've touched on only ONE page:
                         the thin edges of what you've read.

Reconstructing this arrangement in memory is exactly what the "connect the
concepts to each other" community rung would MATERIALIZE as graph edges.
The script shows the payoff before the rung is built.

Honest limits (say them aloud): a "frontier" concept is UNOBSERVED
elsewhere in your record, not "not understood" — exposure is not mastery.
Pages processed while the WikiData breaker was open have truncated
ancestry, so some concepts sit as their own small islands (best-effort
coverage). WikiData's crowd-sourced hierarchy is a DAG, not a clean tree:
a concept can nest under more than one parent, and this map preserves
that. Nothing here is an LLM's opinion — it's a deterministic read of the
record.

USAGE (from the host, stack running)
------------------------------------
  docker exec coyote-coyote_app-1 python /app/coyote/demos/knowledge_shape.py
      Coverage summary + concept hubs.
  docker exec coyote-coyote_app-1 python /app/coyote/demos/knowledge_shape.py --tree
      The full reconstructed knowledge tree.
  docker exec coyote-coyote_app-1 python /app/coyote/demos/knowledge_shape.py --concept "learning"
      The subtree around concepts whose label matches the substring.
  docker exec coyote-coyote_app-1 python /app/coyote/demos/knowledge_shape.py --html /app/data/knowledge_shape.html
      Write a self-contained interactive visual (open in a browser). The
      file is standalone — no server, no internet, safe to share.
  Options: --top K (hubs / children shown per node, default 12),
           --max-depth D (tree print depth, default 6), --json.
"""

import argparse
import json
import logging
import sqlite3
import sys
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# WikiData relations that express "is-a-kind-of / is-an-instance-of" — the
# taxonomic backbone. The cache also stores "part of" (P361) and "said to
# be the same as" (P460); those aren't strict hierarchy, so the default
# map excludes them (kept configurable for anyone who wants meronymy in).
HIERARCHICAL_RELATIONS = ("subclass of", "instance of")

# Safety bounds on the upward walk so a pathological cache can't run away.
MAX_WALK_DEPTH = 30
MAX_NODES = 20000

_TRACKING_PARAMS = ("srsltid", "gclid", "fbclid")


# ── pure functions (stdlib only; unit-tested host-side) ──────────────────

def normalize_url(url):
    """
    Canonical page identity for counting: drop the fragment and
    click-tracking query params so a page browsed via a Google result
    (?srsltid=...) or revisited at an anchor counts once. Mirrors the
    source-inference demo's normalizer (kept local so each demo stands
    alone).
    """
    if not url:
        return url
    parts = urlsplit(url)
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k not in _TRACKING_PARAMS and not k.startswith("utm_")
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def extract_leaf_rows(records):
    """
    Pull the DIRECT (leaf) concepts a page was actually about from its
    topics/entities JSON — NOT from its HAS_TOPIC edges, which in the flat
    model also fan out to every ancestor and would swamp the map with
    generic categories ("written work", "human"). Each JSON entry carries
    the disambiguated `wikidata_uri` + `label`; entries with a null uri
    (unresolved terms) are skipped. Yields {uri, label, url} rows.
    `records` = iterable of dicts with url + topics + entities (JSON strings).
    """
    rows = []
    for rec in records:
        url = rec.get("url")
        for field in ("topics", "entities"):
            raw = rec.get(field)
            if not raw:
                continue
            try:
                items = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(items, list):
                continue
            for it in items:
                if not isinstance(it, dict):
                    continue
                uri = it.get("wikidata_uri")
                if not uri:
                    continue
                rows.append({"uri": uri, "label": it.get("label") or uri, "url": url})
    return rows


def aggregate_touched(rows):
    """
    Collapse (uri, label, url) rows into per-concept records.
    Returns {uri: {"label": str, "pages": int}} where `pages` counts
    DISTINCT normalized page URLs — the reading weight behind the concept.
    `rows` = iterable of dicts with uri/label/url.
    """
    pages = defaultdict(set)
    labels = {}
    for r in rows:
        uri = r.get("uri")
        if not uri:
            continue
        u = normalize_url(r.get("url"))
        if u:
            pages[uri].add(u)
        if r.get("label") and uri not in labels:
            labels[uri] = r["label"]
    return {
        uri: {"label": labels.get(uri, uri), "pages": len(urls)}
        for uri, urls in pages.items()
    }


def parse_parent_map(cache_rows, relations=HIERARCHICAL_RELATIONS):
    """
    From raw (uri, data_json) cache rows, build {uri: [(parent_uri, label)]}
    keeping only hierarchical relations. Malformed rows are skipped, not
    fatal — the cache is best-effort.
    """
    parent_map = {}
    for uri, data_json in cache_rows:
        try:
            entries = json.loads(data_json) if data_json else []
        except (ValueError, TypeError):
            continue
        parents = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            if e.get("relationship") not in relations:
                continue
            puri = e.get("parent")
            if puri:
                parents.append((puri, e.get("parentLabel") or puri))
        if parents:
            parent_map[uri] = parents
    return parent_map


def build_dag(touched, parent_map, max_depth=MAX_WALK_DEPTH, max_nodes=MAX_NODES):
    """
    Reconstruct the ontology slice covered by `touched` concepts by walking
    UP through `parent_map`. Returns {uri: node} where each node has:
      label, pages (0 for structural ancestors), touched (bool),
      parents (set of uris), children (set of uris).
    Cycle- and size-guarded (WikiData P279/P31 has rare cycles + is large).
    """
    nodes = {}

    def ensure(uri, label, touched_flag, pages):
        n = nodes.get(uri)
        if n is None:
            n = {"label": label, "pages": pages, "touched": touched_flag,
                 "parents": set(), "children": set()}
            nodes[uri] = n
        else:
            if touched_flag:
                n["touched"] = True
                n["pages"] = pages
                n["label"] = label
        return n

    frontier = deque()
    for uri, rec in touched.items():
        ensure(uri, rec["label"], True, rec["pages"])
        frontier.append((uri, 0))

    while frontier:
        uri, depth = frontier.popleft()
        if depth >= max_depth or len(nodes) >= max_nodes:
            continue
        for parent_uri, parent_label in parent_map.get(uri, ()):
            if parent_uri == uri:  # self-loop guard
                continue
            newly = parent_uri not in nodes
            ensure(parent_uri, parent_label, False, 0)
            nodes[uri]["parents"].add(parent_uri)
            nodes[parent_uri]["children"].add(uri)
            if newly:
                frontier.append((parent_uri, depth + 1))
    return nodes


def find_roots(nodes):
    """Uris with no parent inside the reconstructed slice — your broad areas."""
    return sorted(u for u, n in nodes.items() if not n["parents"])


def compute_depths(nodes, roots):
    """Shortest depth (root = 0) for every reachable node, BFS from roots."""
    depth = {r: 0 for r in roots}
    q = deque(roots)
    while q:
        u = q.popleft()
        for c in nodes[u]["children"]:
            if c not in depth:
                depth[c] = depth[u] + 1
                q.append(c)
    # Nodes unreachable from any root (cycle islands) get depth 0.
    for u in nodes:
        depth.setdefault(u, 0)
    return depth


def subtree_page_coverage(nodes):
    """
    Distinct touched pages reachable at/under each node (a concept's
    reading weight incl. everything more specific). Because the structure
    is a DAG, a page can be counted under more than one ancestor — that is
    correct (the concept genuinely sits under both).
    """
    coverage = {}

    def visit(uri, stack):
        if uri in coverage:
            return coverage[uri]
        if uri in stack:  # cycle guard
            return set()
        stack.add(uri)
        acc = set()
        n = nodes[uri]
        if n["touched"] and n["pages"] > 0:
            acc.add(uri)  # count the concept itself as one unit of coverage
        for c in n["children"]:
            acc |= visit(c, stack)
        stack.discard(uri)
        coverage[uri] = acc
        return acc

    for uri in nodes:
        visit(uri, set())
    return {u: len(s) for u, s in coverage.items()}


def rank_hubs(touched, top_k=None):
    """Touched concepts by reading weight (pages), deterministic tie-break."""
    ranked = sorted(
        ({"uri": u, "label": r["label"], "pages": r["pages"]}
         for u, r in touched.items()),
        key=lambda d: (-d["pages"], d["label"]),
    )
    return ranked[:top_k] if top_k else ranked


def frontier_concepts(nodes):
    """
    Touched concepts that are LEAVES (nothing more specific in your record
    hangs under them) and sit on exactly one page — the thin edges of what
    you've read. Unobserved-elsewhere, NOT unknown.
    """
    out = []
    for u, n in nodes.items():
        if not n["touched"] or n["pages"] != 1:
            continue
        if any(nodes[c]["touched"] for c in n["children"]):
            continue
        out.append({"uri": u, "label": n["label"]})
    return sorted(out, key=lambda d: d["label"])


def coverage_summary(nodes, touched, roots, depths, distinct_pages):
    """Headline counts for the record's shape."""
    touched_depths = [depths[u] for u in nodes if nodes[u]["touched"]]
    return {
        "concepts_touched": len(touched),
        "distinct_pages": distinct_pages,
        "broad_areas": sum(1 for r in roots if nodes[r]["children"]),
        "structural_ancestors": sum(1 for n in nodes.values() if not n["touched"]),
        "max_depth": max(touched_depths) if touched_depths else 0,
    }


# ── graph + cache access (container-only imports kept out of module top) ──

def _connect_neo4j():
    logging.getLogger("neo4j").setLevel(logging.ERROR)
    app_root = Path(__file__).resolve().parents[2]  # /app
    sys.path.insert(0, str(app_root))
    from coyote.utils.config_manager import connect_to_neo4j
    return connect_to_neo4j()


def fetch_webpage_concepts(driver):
    """Each page's own topics/entities JSON — the leaf concepts it was about."""
    cypher = """
    MATCH (w:Webpage)
    WHERE w.topics IS NOT NULL OR w.entities IS NOT NULL
    RETURN w.url AS url, w.topics AS topics, w.entities AS entities
    """
    with driver.session() as session:
        return [dict(r) for r in session.run(cypher)]


def fetch_cache_rows():
    from coyote.utils.config_container import WIKIDATA_CACHE_DB_FILE
    with sqlite3.connect(WIKIDATA_CACHE_DB_FILE) as conn:
        return conn.execute("SELECT uri, data FROM wikidata_cache").fetchall()


def load_shape():
    """Read both local stores and reconstruct the DAG. Returns the bundle."""
    driver = _connect_neo4j()
    try:
        rows = extract_leaf_rows(fetch_webpage_concepts(driver))
    finally:
        driver.close()
    touched = aggregate_touched(rows)
    distinct_pages = len({normalize_url(r["url"]) for r in rows if r.get("url")})
    parent_map = parse_parent_map(fetch_cache_rows())
    nodes = build_dag(touched, parent_map)
    roots = find_roots(nodes)
    depths = compute_depths(nodes, roots)
    return {"touched": touched, "nodes": nodes, "roots": roots,
            "depths": depths, "distinct_pages": distinct_pages}


# ── rendering ─────────────────────────────────────────────────────────────

def render_summary(bundle):
    s = coverage_summary(bundle["nodes"], bundle["touched"], bundle["roots"],
                         bundle["depths"], bundle["distinct_pages"])
    L = []
    L.append("=" * 72)
    L.append("THE SHAPE OF WHAT YOU'VE READ")
    L.append("=" * 72)
    L.append(f"  {s['concepts_touched']:>5} distinct concepts read about, "
             f"across {s['distinct_pages']} pages")
    L.append(f"  {s['broad_areas']:>5} broad areas (top-level categories your reading spans)")
    L.append(f"  {s['structural_ancestors']:>5} connecting ancestors pulled in to link them")
    L.append(f"  {s['max_depth']:>5} = deepest specificity (steps from a broad area to your most specific read)")
    return "\n".join(L)


def render_hubs(bundle, top_k):
    ranked = rank_hubs(bundle["touched"], top_k)
    L = ["", "CONCEPT HUBS — what the most of your reading orbits:"]
    for i, h in enumerate(ranked, start=1):
        L.append(f"  {i:>2}. {h['pages']:>3} pages  {h['label']}")
    return "\n".join(L)


def render_frontier(bundle, top_k):
    fr = frontier_concepts(bundle["nodes"])
    L = ["", f"FRONTIER — specific concepts touched on a single page "
             f"({len(fr)} total; unobserved elsewhere, not unmastered):"]
    for f in fr[:top_k]:
        L.append(f"  · {f['label']}")
    if len(fr) > top_k:
        L.append(f"  … +{len(fr) - top_k} more")
    return "\n".join(L)


def render_tree(bundle, max_depth, max_children, roots=None):
    nodes = bundle["nodes"]
    cov = subtree_page_coverage(nodes)
    roots = roots if roots is not None else bundle["roots"]
    # Broadest-coverage roots first.
    roots = sorted(roots, key=lambda u: (-cov.get(u, 0), nodes[u]["label"]))
    L = ["", "KNOWLEDGE TREE (reconstructed from the ontology cache):"]
    seen = set()

    def walk(uri, depth):
        if depth > max_depth:
            return
        n = nodes[uri]
        indent = "  " + "   " * depth
        tag = f"  [{n['pages']} pages]" if n["touched"] and n["pages"] else ""
        dim = "" if n["touched"] else "·"  # dim connecting ancestors
        L.append(f"{indent}{dim}{n['label']}{tag}")
        if uri in seen:  # DAG re-entry — show once expanded
            if n["children"]:
                L.append(f"{indent}   … (also appears above)")
            return
        seen.add(uri)
        kids = sorted(n["children"], key=lambda u: (-cov.get(u, 0), nodes[u]["label"]))
        for c in kids[:max_children]:
            walk(c, depth + 1)
        if len(kids) > max_children:
            L.append(f"{indent}   … +{len(kids) - max_children} more concepts")

    for r in roots:
        walk(r, 0)
    return "\n".join(L)


def build_graph_payload(bundle):
    """
    Flatten the reconstructed DAG into a compact {nodes, links, summary}
    payload for the browser visual. Node indices follow `nodes` insertion
    order so `links` can reference them by integer. Each node carries:
      label, pages (0 for structural ancestors), cov (subtree page
      coverage — its reading weight incl. everything more specific),
      t (1 = a concept you read about, 0 = a connecting ancestor),
      d (depth from a broad area). Pure/host-testable.
    """
    nodes = bundle["nodes"]
    cov = subtree_page_coverage(nodes)
    depths = bundle["depths"]
    idx = {uri: i for i, uri in enumerate(nodes)}
    out_nodes = [
        {"label": n["label"], "pages": n["pages"], "cov": cov.get(uri, 0),
         "t": 1 if n["touched"] else 0, "d": depths.get(uri, 0)}
        for uri, n in nodes.items()
    ]
    links = [[idx[uri], idx[c]] for uri, n in nodes.items() for c in n["children"]]
    summary = coverage_summary(nodes, bundle["touched"], bundle["roots"],
                               depths, bundle["distinct_pages"])
    return {"nodes": out_nodes, "links": links, "summary": summary}


def render_html(bundle, title="The shape of what you've read"):
    """
    A self-contained interactive node-link map of the reconstructed
    ontology slice. No external assets (no CDN, works offline, shareable).
    Concepts you read about glow and are sized by how much of your reading
    sits at or under them; connecting ancestors are dim. A coverage slider
    collapses the single-page dust so the hubs — the shape — stand out.
    """
    payload = json.dumps(build_graph_payload(bundle), separators=(",", ":"))
    return (_HTML_TEMPLATE
            .replace("__TITLE__", title)
            .replace("__PAYLOAD__", payload))


# The visual is deliberately one file with zero dependencies. The force
# simulation is a compact Fruchterman-Reingold on a canvas; picking, pan,
# zoom and node-drag are hand-rolled so nothing has to be fetched.
_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; }
  body {
    background: #0c1020; color: #e8ecf6;
    font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    overflow: hidden;
  }
  #stage { position: fixed; inset: 0; }
  canvas { display: block; cursor: grab; }
  canvas.grabbing { cursor: grabbing; }
  #panel {
    position: fixed; top: 16px; left: 16px; width: 320px; max-width: calc(100vw - 32px);
    background: rgba(16,21,40,.82); backdrop-filter: blur(8px);
    border: 1px solid rgba(120,140,200,.22); border-radius: 12px;
    padding: 16px 18px; box-shadow: 0 8px 30px rgba(0,0,0,.4);
  }
  #panel h1 { margin: 0 0 4px; font-size: 16px; font-weight: 650; letter-spacing: .2px; }
  #panel .sub { margin: 0 0 12px; color: #9aa6c8; font-size: 12px; }
  .stats { display: grid; grid-template-columns: auto 1fr; gap: 2px 10px; margin: 0 0 14px; }
  .stats b { color: #ffd7a0; font-variant-numeric: tabular-nums; text-align: right; }
  .stats span { color: #aeb9da; font-size: 12.5px; }
  .ctl { margin: 10px 0; }
  .ctl label { display: block; font-size: 12px; color: #aeb9da; margin-bottom: 4px; }
  .ctl input[type=range] { width: 100%; }
  .ctl input[type=search] {
    width: 100%; padding: 6px 8px; border-radius: 7px; border: 1px solid rgba(120,140,200,.3);
    background: #0f1530; color: #e8ecf6; font: inherit;
  }
  .legend { display: flex; gap: 14px; font-size: 11.5px; color: #9aa6c8; margin-top: 6px; }
  .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 5px; vertical-align: -1px; }
  .note { margin-top: 12px; font-size: 11px; color: #7b86a8; line-height: 1.45; }
  #tip {
    position: fixed; pointer-events: none; z-index: 5; display: none;
    background: rgba(10,14,28,.95); border: 1px solid rgba(140,160,220,.35);
    border-radius: 8px; padding: 7px 10px; font-size: 12.5px; max-width: 260px;
    box-shadow: 0 6px 20px rgba(0,0,0,.5);
  }
  #tip b { color: #ffd7a0; }
  #tip .r { color: #9aa6c8; font-size: 11px; }
  #reset {
    margin-top: 8px; padding: 5px 10px; font: inherit; font-size: 12px; cursor: pointer;
    background: #1a2140; color: #cdd6f4; border: 1px solid rgba(120,140,200,.3); border-radius: 7px;
  }
  #reset:hover { background: #232c52; }
  #count { color: #7b86a8; font-size: 11px; margin-left: 6px; }
</style>
</head>
<body>
<div id="stage"><canvas id="c"></canvas></div>
<div id="tip"></div>
<div id="panel">
  <h1>__TITLE__</h1>
  <p class="sub">A deterministic read of your Coyote record — nothing here is an LLM's opinion.</p>
  <div class="stats" id="stats"></div>
  <div class="ctl">
    <label>Show concepts read on ≥ <b id="thv">2</b> pages<span id="count"></span></label>
    <input type="range" id="thr" min="1" max="10" value="2" step="1">
  </div>
  <div class="ctl">
    <label>Find a concept</label>
    <input type="search" id="q" placeholder="e.g. learning, mushroom, Firefox">
  </div>
  <div class="legend">
    <span><i style="background:#ffb75e"></i>read about</span>
    <span><i style="background:#3f4a6b"></i>connecting ancestor</span>
    <span>bigger = more pages you read it on</span>
  </div>
  <button id="reset">reset view</button>
  <p class="note">Slide to 1 to reveal the frontier — concepts touched on a single
    page, <em>unobserved elsewhere</em> in your record (exposure, not mastery).
    Dim nodes are connecting ancestors from WikiData; ancestry is best-effort, so
    some concepts float as their own islands.</p>
</div>
<script>
const DATA = __PAYLOAD__;
const cv = document.getElementById('c'), ctx = cv.getContext('2d');
const tip = document.getElementById('tip');
let DPR = Math.min(window.devicePixelRatio || 1, 2);
let W = 0, H = 0;

// ── camera ────────────────────────────────────────────────────────────
let scale = 1, ox = 0, oy = 0;   // screen = world*scale + offset
function resize() {
  W = window.innerWidth; H = window.innerHeight;
  cv.width = W * DPR; cv.height = H * DPR;
  cv.style.width = W + 'px'; cv.style.height = H + 'px';
}
window.addEventListener('resize', () => { resize(); });
resize();

// ── stats panel ───────────────────────────────────────────────────────
const s = DATA.summary;
document.getElementById('stats').innerHTML =
  `<b>${s.concepts_touched}</b><span>concepts read about</span>` +
  `<b>${s.distinct_pages}</b><span>pages</span>` +
  `<b>${s.broad_areas}</b><span>broad areas</span>` +
  `<b>${s.max_depth}</b><span>deepest specificity</span>`;

// ── build node/link objects ───────────────────────────────────────────
const maxCov = DATA.nodes.reduce((m, n) => Math.max(m, n.cov), 1);
const maxPages = DATA.nodes.reduce((m, n) => n.t ? Math.max(m, n.pages) : m, 1);
const nodes = DATA.nodes.map((n, i) => ({
  i, label: n.label, pages: n.pages, cov: n.cov, t: n.t, d: n.d,
  // Touched concepts are sized by YOUR reading weight (distinct pages);
  // connecting ancestors are thin scaffolding so generic classes like
  // "written work" never become giants.
  r: n.t ? (3 + 3.2 * Math.sqrt(n.pages)) : 2.3,
  x: Math.cos(i * 2.399) * (40 + i % 400),   // golden-angle seed spread
  y: Math.sin(i * 2.399) * (40 + i % 400),
  vx: 0, vy: 0, fixed: false, vis: false
}));
const links = DATA.links.map(l => ({ a: nodes[l[0]], b: nodes[l[1]] }));

// adjacency (undirected) for hover-highlight; directed children (parent→
// child, i.e. toward the more specific) for the "does this ancestor connect
// visible reading?" test used when thresholding.
const adj = nodes.map(() => []);
const kids = nodes.map(() => []);
links.forEach(l => { adj[l.a.i].push(l.b.i); adj[l.b.i].push(l.a.i); kids[l.a.i].push(l.b.i); });

// ── visibility (coverage threshold) ───────────────────────────────────
let threshold = 2, visNodes = [], visLinks = [];
function applyThreshold() {
  // A concept you read about appears if it meets the page threshold.
  const touchedVis = n => n.t && n.pages >= threshold;
  // An ancestor appears only if it connects >=2 visible concepts — real
  // shared structure, not a dangling chain down to a single reading.
  const memo = new Array(nodes.length).fill(-1);
  function descCount(i, stack) {
    if (memo[i] !== -1) return memo[i];
    if (stack.has(i)) return 0;              // cycle guard
    stack.add(i);
    let acc = touchedVis(nodes[i]) ? 1 : 0;
    for (const c of kids[i]) acc += descCount(c, stack);
    stack.delete(i);
    memo[i] = acc; return acc;
  }
  visNodes = nodes.filter(n =>
    touchedVis(n) || (!n.t && descCount(n.i, new Set()) >= 2));
  const set = new Set(visNodes.map(n => n.i));
  visLinks = links.filter(l => set.has(l.a.i) && set.has(l.b.i));
  document.getElementById('count').textContent =
    '  (' + visNodes.filter(n => n.t).length + ' concepts + ' +
    visNodes.filter(n => !n.t).length + ' links)';
  temp = 0.9;  // reheat the simulation
}

// ── force simulation (Fruchterman-Reingold, cooled) ───────────────────
let temp = 0.9;
const K = 46;                    // ideal edge length in world units
function step() {
  const N = visNodes.length;
  if (!N) return;
  // repulsion (O(n^2) over the VISIBLE set only — small once thresholded)
  for (let i = 0; i < N; i++) {
    const a = visNodes[i];
    for (let j = i + 1; j < N; j++) {
      const b = visNodes[j];
      let dx = a.x - b.x, dy = a.y - b.y;
      let d2 = dx * dx + dy * dy || 0.01;
      let d = Math.sqrt(d2);
      let f = (K * K) / d2;
      let fx = (dx / d) * f, fy = (dy / d) * f;
      a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
    }
  }
  // attraction along visible links
  for (const l of visLinks) {
    let dx = l.a.x - l.b.x, dy = l.a.y - l.b.y;
    let d = Math.sqrt(dx * dx + dy * dy) || 0.01;
    let f = (d * d) / K;
    let fx = (dx / d) * f, fy = (dy / d) * f;
    l.a.vx -= fx; l.a.vy -= fy; l.b.vx += fx; l.b.vy += fy;
  }
  // integrate with gravity to center + cooling cap on displacement
  const cap = 30 * temp;
  for (const n of visNodes) {
    n.vx -= n.x * 0.012; n.vy -= n.y * 0.012;         // gentle centering
    let sp = Math.sqrt(n.vx * n.vx + n.vy * n.vy) || 0.01;
    let m = Math.min(sp, cap);
    if (!n.fixed) { n.x += (n.vx / sp) * m; n.y += (n.vy / sp) * m; }
    n.vx *= 0.82; n.vy *= 0.82;
  }
  if (temp > 0.02) temp *= 0.994;
}

// ── draw ──────────────────────────────────────────────────────────────
let hover = null;
function color(n) {
  if (!n.t) return '#3f4a6b';                             // dim scaffolding
  const f = Math.min(1, Math.sqrt(n.pages / maxPages));   // warm ramp by pages
  const g = Math.round(140 + 70 * (1 - f)), b = Math.round(60 + 45 * (1 - f));
  return `rgb(255,${g},${b})`;
}
function draw() {
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.save();
  ctx.translate(ox, oy); ctx.scale(scale, scale);

  const hl = hover ? new Set([hover.i, ...adj[hover.i]]) : null;

  // links
  ctx.lineWidth = 0.6 / scale;
  for (const l of visLinks) {
    const on = hl && (l.a.i === hover.i || l.b.i === hover.i);
    ctx.strokeStyle = on ? 'rgba(255,200,120,.7)' : 'rgba(140,160,220,.12)';
    ctx.beginPath(); ctx.moveTo(l.a.x, l.a.y); ctx.lineTo(l.b.x, l.b.y); ctx.stroke();
  }
  // nodes
  for (const n of visNodes) {
    const dim = hl && !hl.has(n.i);
    ctx.globalAlpha = dim ? 0.22 : 1;
    ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, 6.2832);
    ctx.fillStyle = n.match ? '#7fd6ff' : color(n);
    ctx.fill();
    if (n.t && n.pages >= 2) { ctx.lineWidth = 0.5 / scale; ctx.strokeStyle = 'rgba(255,255,255,.25)'; ctx.stroke(); }
  }
  ctx.globalAlpha = 1;
  // labels for the biggest / hovered / matched
  ctx.fillStyle = '#e8ecf6';
  const fs = Math.max(10, 12 / scale);
  ctx.font = fs + 'px sans-serif';
  for (const n of visNodes) {
    const big = n.t && n.pages >= Math.max(2, maxPages * 0.35);
    if (!(big || n.match || (hover && (n.i === hover.i || adj[hover.i].includes(n.i))))) continue;
    ctx.globalAlpha = (hover && !(n.i === hover.i || adj[hover.i].includes(n.i)) && !n.match) ? 0.5 : 1;
    ctx.fillText(n.label, n.x + n.r + 2, n.y + 3);
  }
  ctx.globalAlpha = 1;
  ctx.restore();
}
function frame() { step(); draw(); requestAnimationFrame(frame); }

// ── picking + interaction ─────────────────────────────────────────────
function toWorld(px, py) { return { x: (px - ox) / scale, y: (py - oy) / scale }; }
function pick(px, py) {
  const w = toWorld(px, py);
  let best = null, bd = 1e9;
  for (const n of visNodes) {
    const dx = n.x - w.x, dy = n.y - w.y, d = dx * dx + dy * dy;
    const rr = (n.r + 4) * (n.r + 4);
    if (d < rr && d < bd) { bd = d; best = n; }
  }
  return best;
}
let drag = null, panning = false, lastx = 0, lasty = 0, moved = false;
cv.addEventListener('mousedown', e => {
  moved = false;
  const n = pick(e.clientX, e.clientY);
  if (n) { drag = n; n.fixed = true; }
  else { panning = true; cv.classList.add('grabbing'); }
  lastx = e.clientX; lasty = e.clientY;
});
window.addEventListener('mousemove', e => {
  if (drag) {
    const w = toWorld(e.clientX, e.clientY);
    drag.x = w.x; drag.y = w.y; drag.vx = drag.vy = 0; temp = Math.max(temp, 0.25); moved = true;
  } else if (panning) {
    ox += e.clientX - lastx; oy += e.clientY - lasty;
    lastx = e.clientX; lasty = e.clientY; moved = true;
  } else {
    const n = pick(e.clientX, e.clientY);
    hover = n;
    if (n) {
      tip.style.display = 'block';
      tip.style.left = (e.clientX + 14) + 'px'; tip.style.top = (e.clientY + 12) + 'px';
      if (n.t) {
        const under = n.cov > 1 ? ` · ${n.cov - 1} more specific under it` : '';
        tip.innerHTML = `<b>${n.label}</b><br><span class="r">${n.pages} page${n.pages === 1 ? '' : 's'}${under} · concept you read about</span>`;
      } else {
        tip.innerHTML = `<b>${n.label}</b><br><span class="r">${n.cov} of your concepts under it · connecting ancestor</span>`;
      }
    } else { tip.style.display = 'none'; }
  }
});
window.addEventListener('mouseup', () => {
  if (drag && !moved) drag.fixed = false;   // a click (not a drag) releases the pin
  drag = null; panning = false; cv.classList.remove('grabbing');
});
cv.addEventListener('wheel', e => {
  e.preventDefault();
  const f = Math.exp(-e.deltaY * 0.0012);
  const wx = (e.clientX - ox) / scale, wy = (e.clientY - oy) / scale;
  scale *= f;
  ox = e.clientX - wx * scale; oy = e.clientY - wy * scale;
}, { passive: false });

// ── search highlight ──────────────────────────────────────────────────
document.getElementById('q').addEventListener('input', e => {
  const q = e.target.value.trim().toLowerCase();
  let first = null;
  for (const n of nodes) {
    n.match = q && n.label.toLowerCase().includes(q);
    if (n.match && !first) first = n;
  }
  if (q && first) { // pan to the first match at current zoom
    ox = W * 0.6 - first.x * scale; oy = H * 0.5 - first.y * scale;
  }
});

// ── coverage slider ───────────────────────────────────────────────────
const thr = document.getElementById('thr'), thv = document.getElementById('thv');
thr.addEventListener('input', () => {
  threshold = +thr.value; thv.textContent = threshold; applyThreshold();
});

// ── reset ─────────────────────────────────────────────────────────────
function fitView() {
  if (!visNodes.length) { scale = 1; ox = W / 2; oy = H / 2; return; }
  let minx = 1e9, miny = 1e9, maxx = -1e9, maxy = -1e9;
  for (const n of visNodes) { minx = Math.min(minx, n.x); maxx = Math.max(maxx, n.x); miny = Math.min(miny, n.y); maxy = Math.max(maxy, n.y); }
  const w = (maxx - minx) || 1, h = (maxy - miny) || 1;
  scale = Math.min((W - 380) / w, (H - 80) / h, 2.2) * 0.9;
  scale = Math.max(scale, 0.12);
  ox = (W + 340) / 2 - ((minx + maxx) / 2) * scale;
  oy = H / 2 - ((miny + maxy) / 2) * scale;
}
document.getElementById('reset').addEventListener('click', () => { temp = 0.6; setTimeout(fitView, 300); });

// ── boot ──────────────────────────────────────────────────────────────
thr.max = Math.max(2, maxPages); thr.value = threshold; thv.textContent = threshold;
applyThreshold();
ox = W / 2; oy = H / 2; scale = 0.7;
setTimeout(fitView, 600);   // let the sim spread first, then frame it
frame();
</script>
</body>
</html>
"""


def _match_roots(bundle, needle):
    """Roots whose subtree contains a concept matching the substring."""
    nodes = bundle["nodes"]
    needle = needle.lower()
    hits = {u for u, n in nodes.items() if needle in (n["label"] or "").lower()}
    if not hits:
        return []
    # Walk up from each hit to collect the roots above it.
    roots = set()
    for h in hits:
        stack, seen = [h], set()
        while stack:
            u = stack.pop()
            if u in seen:
                continue
            seen.add(u)
            if not nodes[u]["parents"]:
                roots.add(u)
            stack.extend(nodes[u]["parents"])
    return sorted(roots)


# ── CLI ───────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Print the reconstructed shape of what your reading covers."
    )
    parser.add_argument("--tree", action="store_true", help="print the full knowledge tree")
    parser.add_argument("--concept", help="focus the tree on concepts matching this substring")
    parser.add_argument("--top", type=int, default=12,
                        help="hubs / children per node to show (default 12)")
    parser.add_argument("--max-depth", type=int, default=6,
                        help="tree print depth (default 6)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--html", nargs="?", const="knowledge_shape.html",
                        metavar="PATH",
                        help="write a self-contained interactive visual to PATH "
                             "(default knowledge_shape.html) instead of printing")
    args = parser.parse_args(argv)

    bundle = load_shape()
    if not bundle["touched"]:
        print("No concepts in the graph yet. Browse and let the ontology "
              "stage run, then re-run.")
        return 1

    if args.html:
        out = Path(args.html)
        out.write_text(render_html(bundle), encoding="utf-8")
        s = coverage_summary(bundle["nodes"], bundle["touched"], bundle["roots"],
                             bundle["depths"], bundle["distinct_pages"])
        print(f"Wrote {out}  ({s['concepts_touched']} concepts / "
              f"{s['distinct_pages']} pages / {len(bundle['nodes'])} nodes). "
              f"Open it in a browser.")
        return 0

    if args.json:
        nodes = bundle["nodes"]
        cov = subtree_page_coverage(nodes)
        print(json.dumps({
            "summary": coverage_summary(nodes, bundle["touched"], bundle["roots"],
                                        bundle["depths"], bundle["distinct_pages"]),
            "hubs": rank_hubs(bundle["touched"], args.top),
            "frontier": frontier_concepts(nodes),
            "roots": [
                {"uri": r, "label": nodes[r]["label"], "coverage": cov.get(r, 0)}
                for r in bundle["roots"]
            ],
        }, indent=2))
        return 0

    print(render_summary(bundle))
    print(render_hubs(bundle, args.top))

    if args.concept:
        roots = _match_roots(bundle, args.concept)
        if not roots:
            print(f"\nNo concept matching '{args.concept}'.")
        else:
            print(render_tree(bundle, args.max_depth, args.top, roots=roots))
    elif args.tree:
        print(render_tree(bundle, args.max_depth, args.top))
    else:
        print(render_frontier(bundle, args.top))
        print("\n(Run with --tree for the full hierarchy, or "
              "--concept SUBSTRING to focus it.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
