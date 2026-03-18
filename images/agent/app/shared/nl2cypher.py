# shared/nl2cypher.py
# Minimal, dependency-free helpers shared by Chat + Explore Visually.

from __future__ import annotations
import re
from typing import Literal

# Conservative static schema (works without drivers); you can swap in a
# dynamic one where available.
SCHEMA_MIN = """
Node Labels and Properties:
(:Webpage {{event_id, url, title, summary, timestamp, isSERP, dataSource, entities, topics}})
(:Annotation {{annotation_id, annotation_text, highlighted_text, timestamp, url, webpage_title, entities, topics}})
(:Purpose {{event_id, text, timestamp, dataSource, topics, entities}})
(:SearchTerms {{event_id, text, relevance, timestamp, dataSource, topics, entities}})
(:WikiDataOntology {{uri, label}})

Relationships:
(:Webpage)-[:HAS_TOPIC]->(:WikiDataOntology)
(:Annotation)-[:HAS_TOPIC]->(:WikiDataOntology)
(:Purpose)-[:HAS_TOPIC]->(:WikiDataOntology)
(:Purpose)-[:INITIATES_SEARCH]->(:SearchTerms)
(:Webpage)-[:HAS_ANNOTATION]->(:Annotation)
(:Webpage)-[:LINKS_TO]->(:Webpage)
"""

PROMPT_TABLE = """You are an expert Neo4j/Cypher query assistant for the Coyote personal learning system.

TASK: Generate ONE read-only Cypher query to answer the user's question using ONLY the schema below.

SCHEMA:
{schema}

IMPORTANT RULES:
1. Use ONLY the labels, properties, and relationships listed above.
2. Topics and entities are linked via WikiDataOntology nodes (property: label).
3. To find content about specific topics: (:Webpage)-[:HAS_TOPIC]->(:WikiDataOntology)
4. Use case-insensitive matching with toLower(...).
5. For counts: RETURN count(DISTINCT node) AS numberOfResults
6. For date filtering, use: datetime(node.timestamp) >= datetime() - duration({{days: N}})

OUTPUT RULES (CRITICAL):
- Return only a valid Cypher statement.
- Do not wrap the output in JSON.
- Do not include code fences or a leading language tag.
- The first word of your output must be a Cypher keyword (MATCH/CALL/WITH/...).

USER QUESTION: {question}
"""

PROMPT_GRAPH = """You translate user questions into ONE read-only Cypher query over the schema below.

SCHEMA:
{schema}

CRITICAL RULES:
1. Use ONLY the listed labels/properties/relationships
2. For date filtering: datetime(node.timestamp) >= datetime() - duration({{days: N}})
3. For "today" comparisons: date(datetime(node.timestamp)) = date()
4. ALWAYS use this EXACT pattern for the RETURN statement:

MATCH <your pattern>
WHERE <your filters>
WITH collect(DISTINCT <matched_node>) AS matchedNodes
OPTIONAL MATCH <relationships if needed>
WITH matchedNodes + collect(DISTINCT <related_node>) AS allNodes, collect(DISTINCT <relationship>) AS allRels
RETURN
  [x IN allNodes | {{id:id(x), labels:labels(x), props:properties(x)}}] AS nodes,
  [x IN allRels | {{id:id(x), type:type(x), s:id(startNode(x)), t:id(endNode(x)), props:properties(x)}}] AS rels

EXAMPLES:

Question: "What webpages have I viewed today?"
{{
  "cypher": "MATCH (w:Webpage {{isSERP: false}}) WHERE date(datetime(w.timestamp)) = date() WITH collect(DISTINCT w) AS matchedNodes RETURN [x IN matchedNodes | {{id:id(x), labels:labels(x), props:properties(x)}}] AS nodes, [] AS rels"
}}

Question: "Show annotations from the last 7 days"
{{
  "cypher": "MATCH (a:Annotation) WHERE datetime(a.timestamp) >= datetime() - duration({{days: 7}}) WITH collect(DISTINCT a) AS matchedNodes OPTIONAL MATCH (w:Webpage)-[r:HAS_ANNOTATION]->(a) WITH matchedNodes + collect(DISTINCT w) AS allNodes, collect(DISTINCT r) AS allRels RETURN [x IN allNodes | {{id:id(x), labels:labels(x), props:properties(x)}}] AS nodes, [x IN allRels | {{id:id(x), type:type(x), s:id(startNode(x)), t:id(endNode(x)), props:properties(x)}}] AS rels"
}}

Question: "Show me webpages from yesterday"
{{
  "cypher": "MATCH (w:Webpage) WHERE date(datetime(w.timestamp)) = date() - duration({{days: 1}}) WITH collect(DISTINCT w) AS matchedNodes RETURN [x IN matchedNodes | {{id:id(x), labels:labels(x), props:properties(x)}}] AS nodes, [] AS rels"
}}

KEY POINTS:
- For "today" or specific date comparisons: use date(datetime(node.timestamp)) = date()
- For time ranges: use datetime(node.timestamp) >= datetime() - duration({{days: N}})
- First collect your matched nodes with WITH
- Then optionally get related nodes/relationships  
- Then combine everything into allNodes and allRels
- Finally use the list comprehension pattern on those collections

Output STRICT JSON with a single key:
{{"cypher":"<your query here>"}}

USER QUESTION: {question}
"""

# ---------- tiny API ----------
def prompt_text(mode: Literal["table","graph"]="table") -> str:
    return PROMPT_TABLE if mode == "table" else PROMPT_GRAPH

def schema_for_prompts(schema: str | None) -> str:
    return schema or SCHEMA_MIN

_WRITE_BLOCKLIST = re.compile(r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|ALTER|GRANT|REVOKE|LOAD\s+CSV|CALL\s+dbms\.|CALL\s+db\.index\.|apoc\.load|apoc\.trigger)\b", re.I)

def is_read_only(cypher: str) -> bool:
    return not _WRITE_BLOCKLIST.search(" ".join((cypher or "").split()))

def strip_fences_or_json(s: str) -> str:
    if not s: return ""
    t = s.strip()
    if t.startswith("```") and t.endswith("```"):
        t = t.strip("`")
        lines = t.splitlines()
        if lines and lines[0].strip().lower() in ("cypher","json"):
            lines = lines[1:]
        t = "\n".join(lines).strip()
    # JSON {"cypher": "..."} → extract
    if t.startswith("{") and '"cypher"' in t:
        try:
            import json
            t = json.loads(t).get("cypher", t) or t
        except Exception:
            pass
    low = t.lower()
    if low.startswith("json\n") or low.startswith("cypher\n"):
        t = "\n".join(t.splitlines()[1:]).strip()
    return t

def looks_like_cypher(s: str) -> bool:
    kw = ("MATCH","CALL","WITH","UNWIND","MERGE","CREATE","RETURN","EXPLAIN","PROFILE")
    return bool(s) and s.lstrip().upper().startswith(kw)