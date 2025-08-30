import re
import logging, os, sys
import uuid, platform, time
import streamlit as st
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.output_parsers import StrOutputParser
from langchain_neo4j import Neo4jGraph
from dotenv import load_dotenv
from utils import (
    create_vector_index,
)
from chains import (
    load_embedding_model,
    load_llm,
    configure_llm_only_chain,
    configure_qa_rag_chain,
    generate_ticket,
)
from coyote_schema import get_schema
from langchain_neo4j.chains.graph_qa.cypher import GraphCypherQAChain
from langchain_core.prompts import ChatPromptTemplate
from typing import Dict, Any, List
from neo4j.exceptions import Neo4jError
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from logging_config import configure_logging
from obs import trace, trunc

load_dotenv(".env")

url = os.getenv("NEO4J_URI")
username = os.getenv("NEO4J_USERNAME")
password = os.getenv("NEO4J_PASSWORD")
ollama_base_url = os.getenv("OLLAMA_BASE_URL")
embedding_model_name = os.getenv("EMBEDDING_MODEL")
llm_name = os.getenv("LLM")
# Remapping for Langchain Neo4j integration
os.environ["NEO4J_URL"] = url


LOG_DIR = os.getenv("AGENT_LOG_DIR", "/app/logs")
logger = configure_logging(log_dir=LOG_DIR, level=os.getenv("AGENT_LOG_LEVEL"))
logger.info("Agent boot: python=%s platform=%s", sys.version.split()[0], platform.platform())
logger.info("Env: NEO4J_URI=%s OLLAMA_BASE_URL=%s LLM=%s EMBEDDING_MODEL=%s",
            url, ollama_base_url, llm_name, embedding_model_name)

# if Neo4j is local, you can go to http://localhost:7474/ to browse the database
neo4j_graph = Neo4jGraph(
    url=url, username=username, password=password, refresh_schema=False
)
with trace(logger, "neo4j.healthcheck"):
    try:
        ok = neo4j_graph.query("RETURN 1 AS ok")
        logger.debug("neo4j ok=%s", ok[0].get("ok") if ok else None)
    except Exception as e:
        logger.error("neo4j connectivity check failed: %s", e)


with trace(logger, "schema.fetch"):
    try:
        schema = get_schema(url, username, password)
        logger.debug("schema.length=%d", len(schema or ""))
    except Exception as e:
        logger.error("schema fetch failed: %s", e)
        schema = ""  # keep running; canned queries still work

embeddings, dimension = load_embedding_model(
    embedding_model_name, config={"ollama_base_url": ollama_base_url}, logger=logger
)
with trace(logger, "neo4j.ensure_vector_indexes"):
    create_vector_index(neo4j_graph)

def fresh_schema_str() -> str:
    # force refresh so Neo4jGraph introspects again
    neo4j_graph.refresh_schema()
    return get_schema(url, username, password)

class StreamHandler(BaseCallbackHandler):
    def __init__(self, container, initial_text=""):
        self.container = container
        self.text = initial_text

    def on_llm_new_token(self, token: str, **kwargs) -> None:
        self.text += token
        self.container.markdown(self.text)


llm = load_llm(llm_name, logger=logger, config={"ollama_base_url": ollama_base_url})

# ── LLM output sanitizer (fallback-only) ─────────────────────────────────
def _strip_fences_or_json(s: str) -> str:
    """
    Best-effort cleanup for models that occasionally wrap Cypher in ``` fences
    or JSON despite instructions. Used only for logging/debug fallback.
    """
    if s is None:
        return ""
    s = s.strip()
    # remove surrounding code fences
    if s.startswith("```") and s.endswith("```"):
        s = s.strip("`")
        lines = s.splitlines()
        # drop optional language tag on the first line
        if lines and lines[0].strip().lower() in ("cypher", "json"):
            lines = lines[1:]
        s = "\n".join(lines).strip()
    # if it's JSON with a "cypher" field, extract it
    if s.startswith("{") and '"cypher"' in s:
        try:
            import json
            s = json.loads(s).get("cypher", s)
        except Exception:
            pass
    # kill accidental leading language tag tokens
    low = s.lower()
    if low.startswith("json\n") or low.startswith("cypher\n"):
        s = "\n".join(s.splitlines()[1:]).strip()
    return s

# ── Quick shape check for Cypher (fail-fast logging) ─────────────────────
def _looks_like_cypher(s: str) -> bool:
    kw = ("MATCH","CALL","WITH","UNWIND","MERGE","CREATE","RETURN","EXPLAIN","PROFILE")
    return bool(s) and s.lstrip().upper().startswith(kw)

llm_chain = configure_llm_only_chain(llm)
rag_chain = configure_qa_rag_chain(
    llm, embeddings, embeddings_store_url=url, username=username, password=password
)

# ── 3️⃣ direct Cypher chain ───────────────────────────────────────────────

# Define the complete schema
schema_text = """
Node Labels and Properties:
(:Webpage {event_id, url, title, summary, timestamp, isSERP, dataSource, entities, topics})
(:Annotation {annotation_id, annotation_text, highlighted_text, timestamp, url, webpage_title, entities, topics})
(:Purpose {event_id, text, timestamp, dataSource, topics, entities})
(:SearchTerms {event_id, text, relevance, timestamp, dataSource, topics, entities})
(:WikiDataOntology {uri, label})

Relationships:
(:Webpage)-[:HAS_TOPIC]->(:WikiDataOntology)
(:Annotation)-[:HAS_TOPIC]->(:WikiDataOntology)
(:Purpose)-[:HAS_TOPIC]->(:WikiDataOntology)
(:Purpose)-[:INITIATES_SEARCH]->(:SearchTerms)
(:Webpage)-[:HAS_ANNOTATION]->(:Annotation)
(:Webpage)-[:LINKS_TO]->(:Webpage)
"""

CUSTOM_PROMPT = """
You are an expert Neo4j/Cypher query assistant for the Coyote personal learning system.

TASK: Generate ONE Cypher query to answer the user's question using ONLY the schema below.

SCHEMA:
{schema}

IMPORTANT RULES:
1. Use ONLY the labels, properties, and relationships listed above.
2. Topics and entities are linked via WikiDataOntology nodes (property: label).
3. To find content about specific topics: (:Webpage)-[:HAS_TOPIC]->(:WikiDataOntology)
4. Use case-insensitive matching with toLower(...).
5. For counts: RETURN count(DISTINCT node) AS numberOfResults
6. For date filtering, use: datetime(node.timestamp) >= datetime() - duration({{days: N}})
7. The database uses ISO8601 timestamps in a `timestamp` property on nodes listed below; adjust the label accordingly.

OUTPUT RULES (CRITICAL):
- Return **only** a valid Cypher statement.
- Do **not** wrap the output in JSON.
- Do **not** include code fences or a leading language tag (e.g., no ```cypher, no ```json, no 'json').
- The first word of your output must be a Cypher keyword like MATCH, CALL, WITH, or UNWIND.

COMMON PATTERNS:
- Articles about topic X:
  MATCH (w:Webpage)-[:HAS_TOPIC]->(t:WikiDataOntology)
  WHERE toLower(t.label) CONTAINS toLower('X')
  RETURN w

- Annotations about topic X:
  MATCH (a:Annotation)-[:HAS_TOPIC]->(t:WikiDataOntology)
  WHERE toLower(t.label) CONTAINS toLower('X')
  RETURN a

- Recent content within N days:
  WHERE datetime(node.timestamp) >= datetime() - duration({{days: N}})

- "How many ... ?":
  ... RETURN count(DISTINCT node) AS numberOfResults

USER QUESTION: {question}
"""

# Initialize the cypher chain
cypher_prompt = ChatPromptTemplate.from_template(CUSTOM_PROMPT).partial(schema=schema)
cypher_gen = cypher_prompt | llm | StrOutputParser()
USE_LC_NL2CYPHER = os.getenv("USE_LC_NL2CYPHER", "0") == "1"
if USE_LC_NL2CYPHER:
    cypher_chain = GraphCypherQAChain.from_llm(
        llm=llm,
        graph=neo4j_graph,
        cypher_prompt=cypher_prompt,
        verbose=True,
        allow_dangerous_requests=True,
        return_intermediate_steps=True,
        top_k=10  # Return more results by default
    )
logger.info("Cypher-only path: %s", "GraphCypherQAChain" if USE_LC_NL2CYPHER else "custom NL→Cypher + read-only guard")


# ---- CANNED QUERIES (MVP) -----------------------------------------------
CANNED: Dict[str, str] = {
    "count_webpages_since_days": """
        MATCH (w:Webpage)
        WHERE w.timestamp IS NOT NULL
          AND datetime(w.timestamp) >= datetime() - duration({days: $days})
        RETURN count(DISTINCT w) AS numberOfResults
    """,

    "count_annotations_since_days": """
        MATCH (a:Annotation)
        WHERE a.timestamp IS NOT NULL
          AND datetime(a.timestamp) >= datetime() - duration({days: $days})
        RETURN count(DISTINCT a) AS numberOfResults
    """,

    "count_purposes_since_days": """
        MATCH (p:Purpose)
        WHERE p.timestamp IS NOT NULL
          AND datetime(p.timestamp) >= datetime() - duration({days: $days})
        RETURN count(DISTINCT p) AS numberOfResults
    """,

    "list_webpages_by_topic_since_days": """
        MATCH (w:Webpage)-[:HAS_TOPIC]->(t:WikiDataOntology)
        WHERE toLower(t.label) CONTAINS toLower($topic)
          AND w.timestamp IS NOT NULL
          AND datetime(w.timestamp) >= datetime() - duration({days: $days})
        RETURN w.title AS title, w.url AS url, w.timestamp AS timestamp
        ORDER BY timestamp DESC LIMIT $limit
    """,

    "list_annotations_by_topic_since_days": """
        MATCH (a:Annotation)-[:HAS_TOPIC]->(t:WikiDataOntology)
        WHERE toLower(t.label) CONTAINS toLower($topic)
          AND a.timestamp IS NOT NULL
          AND datetime(a.timestamp) >= datetime() - duration({days: $days})
        RETURN a.annotation_text AS text,
               a.webpage_title AS webpage,
               a.timestamp AS timestamp
        ORDER BY timestamp DESC LIMIT $limit
    """,

    "list_searchterms_by_topic_since_days": """
        MATCH (p:Purpose)-[:HAS_TOPIC]->(t:WikiDataOntology)
        WHERE toLower(t.label) CONTAINS toLower($topic)
        MATCH (p)-[:INITIATES_SEARCH]->(s:SearchTerms)
        WHERE s.timestamp IS NOT NULL
          AND datetime(s.timestamp) >= datetime() - duration({days: $days})
        RETURN s.text AS term, s.relevance AS relevance, s.timestamp AS timestamp
        ORDER BY timestamp DESC LIMIT $limit
    """,

    "list_searchterms_since_days": """
        MATCH (p:Purpose)-[:INITIATES_SEARCH]->(s:SearchTerms)
        WHERE s.timestamp IS NOT NULL
          AND datetime(s.timestamp) >= datetime() - duration({days: $days})
        RETURN s.text AS term, s.relevance AS relevance, s.timestamp AS timestamp
        ORDER BY timestamp DESC LIMIT $limit
    """,

    "list_webpages_since_days": """
        MATCH (w:Webpage)
        WHERE w.timestamp IS NOT NULL
          AND datetime(w.timestamp) >= datetime() - duration({days: $days})
        RETURN w.title AS title, w.url AS url, w.timestamp AS timestamp
        ORDER BY timestamp DESC LIMIT $limit
    """,

    "list_annotations_since_days": """
        MATCH (a:Annotation)
        WHERE a.timestamp IS NOT NULL
          AND datetime(a.timestamp) >= datetime() - duration({days: $days})
        RETURN a.annotation_text AS text, a.webpage_title AS webpage, a.timestamp AS timestamp
        ORDER BY timestamp DESC LIMIT $limit
    """,
}



# Streamlit UI
styl = f"""
<style>
    /* not great support for :has yet (hello FireFox), but using it for now */
    .element-container:has([aria-label="Select RAG mode"]) {{
      position: float-left;
      bottom: 33px;
      background: white;
      z-index: 101;
    }}
    .stChatFloatingInputContainer {{
        bottom: 20px;
    }}

    /* Generate ticket text area */
    textarea[aria-label="Description"] {{
        height: 200px;
    }}

    .element-container:has([aria-label="What coding issue can I help you resolve today?"]) {{
        bottom: 45px;
    }} 
</style>
"""
st.markdown(styl, unsafe_allow_html=True)


def chat_input():
    user_input = st.chat_input("What do you want to know about your data?")
    if not user_input:
        return

    with trace(logger, "chat.request", mode=name, chars=len(user_input)):
        with st.chat_message("user"):
            st.write(user_input)

        with st.chat_message("assistant"):
            st.caption(f"Mode: {name}")
            stream_handler = StreamHandler(st.empty())

            try:
                if name == "Cypher-only":
                    if USE_LC_NL2CYPHER:
                        with trace(logger, "lc.GraphCypherQAChain.invoke", q=trunc(user_input, 200)):
                            resp = cypher_chain.invoke(user_input)
                            # GraphCypherQAChain usually returns a dict with 'result'
                            output = resp.get("result") if isinstance(resp, dict) else resp
                            st.markdown(output if isinstance(output, str) else str(output))
                    else:
                        with trace(logger, "cypher_only_answer", q=trunc(user_input, 200)):
                            output = cypher_only_answer(user_input)
                            st.markdown(output)

                elif name == "LLM-only":
                    # llm_chain expects {"question": ...}
                    with trace(logger, "llm_only.invoke", qchars=len(user_input)):
                        output = llm_chain.invoke({"question": user_input}, config={"callbacks": [stream_handler]})

                else:  # GraphRAG
                    with trace(logger, "rag.invoke", qchars=len(user_input)):
                        output = rag_chain.invoke(user_input, config={"callbacks": [stream_handler]})

            except Exception as e:
                logger.exception("chat pipeline error")
                output = f"Something went wrong running your request: {e}"

            st.session_state.setdefault("user_input", []).append(user_input)
            st.session_state.setdefault("generated", []).append(output)
            st.session_state.setdefault("rag_mode", []).append(name)



_WRITE_BLOCKLIST = re.compile(r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP)\b", re.I)

def _is_read_only(cy: str) -> bool:
    return _WRITE_BLOCKLIST.search(cy) is None

def _days_from_text(text: str) -> int:
    t = text.lower()
    # numeric: past 3 days / past 2 weeks / last 6 months
    m = re.search(r"(past|last)\s+(\d+)\s+(day|week|month|year)s?", t)
    if m:
        n = int(m.group(2))
        unit = m.group(3)
        return {"day": 1, "week": 7, "month": 30, "year": 365}[unit] * n
    # common phrases
    if "today" in t: return 1
    if "yesterday" in t: return 2
    if "this week" in t or "past week" in t: return 7
    if "last week" in t: return 7
    if "this month" in t or "past month" in t: return 30
    if "last month" in t: return 30
    if "this year" in t or "past year" in t: return 365
    return 7  # sensible default

def _extract_topic(text: str) -> str | None:
    # quoted topic first: "llms", 'graph theory'
    m = re.search(r"['\"]([^'\"]{3,})['\"]", text)
    if m: return m.group(1).strip()
    # related to/regarding/about <topic>
    m = re.search(r"(related to|about|regarding)\s+([A-Za-z0-9\-_,\s]{3,})", text, re.I)
    if m:
        cand = m.group(2)
        # stop at common time delimiters
        cand = re.split(r"\b(past|last|this|for|since)\b", cand, maxsplit=1)[0]
        return cand.strip(" ,.")
    return None

def _classify_intent(text: str, have_topic: bool, want_count: bool) -> str:
    t = text.lower()
    # counts
    if "purpose" in t and want_count: return "count_purposes_since_days"
    if ("annotation" in t or "note" in t or "highlight" in t) and want_count:
        return "count_annotations_since_days"
    if ("webpage" in t or "article" in t or "page" in t) and want_count:
        return "count_webpages_since_days"

    # lists by topic / generic lists
    if "search term" in t or "query" in t or "queries" in t:
        return "list_searchterms_by_topic_since_days" if have_topic else "list_searchterms_since_days"
    if "annotation" in t or "note" in t or "highlight" in t:
        return "list_annotations_by_topic_since_days" if have_topic else "list_annotations_since_days"
    # default for "what have I read..."
    return "list_webpages_by_topic_since_days" if have_topic else "list_webpages_since_days"

def _format_rows(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "No results."
    # show count if present
    if "numberOfResults" in rows[0]:
        return f"**numberOfResults:** {rows[0]['numberOfResults']}"
    # otherwise a compact markdown table
    cols = list(rows[0].keys())
    lines = ["|" + "|".join(cols) + "|", "|" + "|".join(["---"]*len(cols)) + "|"]
    for r in rows[:20]:
        lines.append("|" + "|".join(str(r.get(c, "")) for c in cols) + "|")
    if len(rows) > 20:
        lines.append(f"\n_Showing 20 of {len(rows)} rows._")
    return "\n".join(lines)

def _run_canned(question: str) -> str:
    days = _days_from_text(question)
    topic = _extract_topic(question)
    want_count = any(w in question.lower() for w in ["how many", "count", "number of"])

    intent = _classify_intent(question, bool(topic), want_count)
    cy = CANNED[intent]
    params = {"days": days, "limit": 25}
    if "$topic" in cy and not topic:
        logger.info("canned intent=%s requires $topic; degrading", intent)
        intent = intent.replace("_by_topic", "")
        cy = CANNED.get(intent, CANNED["list_webpages_since_days"])
    if "$topic" in cy:
        params["topic"] = topic

    logger.info("canned.run intent=%s days=%s topic=%r", intent, days, params.get("topic"))
    with trace(logger, "neo4j.query", kind="canned", intent=intent):
        try:
            rows = neo4j_graph.query(cy, params)
            logger.debug("canned.rows=%d", len(rows))
            return _format_rows(rows)
        except Exception as e:
            logger.error("canned query failed: %s", e)
            return "I couldn't answer that with the canned queries. Try rephrasing or ask for an example."



def cypher_only_answer(question: str) -> str:
    # 1) Generate raw text from LLM
    with trace(logger, "nl2cypher.generate"):
        raw = cypher_gen.invoke({"question": question})
    clean = _strip_fences_or_json(raw or "")
    logger.debug("nl2cypher.raw=%s", trunc(raw, 300))
    logger.debug("nl2cypher.clean=%s", trunc(clean, 300))

    if not _looks_like_cypher(clean):
        logger.warning("nl2cypher produced non-cypher; fallback. head=%r", (clean[:60]))
        return _run_canned(question)
    if not _is_read_only(clean):
        logger.warning("write keyword detected; blocked and fallback")
        return _run_canned(question)

    # 2) Execute
    with trace(logger, "neo4j.query", kind="llm-cypher", q=trunc(clean, 200)):
        try:
            rows = neo4j_graph.query(clean)
            logger.debug("neo4j.rows=%d", len(rows))
            return _format_rows(rows)
        except Neo4jError as e:
            logger.error("neo4j Neo4jError: %s", e)
            return _run_canned(question)
        except Exception as e:
            logger.error("neo4j unexpected error: %s", e)
            return _run_canned(question)



def display_chat():
    # Session state
    if "generated" not in st.session_state:
        st.session_state[f"generated"] = []

    if "user_input" not in st.session_state:
        st.session_state[f"user_input"] = []

    if "rag_mode" not in st.session_state:
        st.session_state[f"rag_mode"] = []

    if st.session_state[f"generated"]:
        size = len(st.session_state[f"generated"])
        # Display only the last three exchanges
        for i in range(max(size - 3, 0), size):
            with st.chat_message("user"):
                st.write(st.session_state[f"user_input"][i])

            with st.chat_message("assistant"):
                st.caption(f"RAG: {st.session_state[f'rag_mode'][i]}")
                st.write(st.session_state[f"generated"][i])

        with st.expander("Not finding what you're looking for?"):
            st.write(
                "Automatically generate a draft for an internal ticket to our support team."
            )
            st.button(
                "Generate ticket",
                type="primary",
                key="show_ticket",
                on_click=open_sidebar,
            )
        with st.container():
            st.write("&nbsp;")


def mode_select() -> str:
    options = ["LLM-only", "GraphRAG", "Cypher-only"]
    return st.radio("Select mode", options, horizontal=True)


name = mode_select()
if name == "LLM-only":
    output_function = llm_chain
elif name == "GraphRAG":
    output_function = rag_chain
elif name == "Cypher-only":
    output_function = cypher_only_answer if not USE_LC_NL2CYPHER else cypher_chain


def open_sidebar():
    st.session_state.open_sidebar = True


def close_sidebar():
    st.session_state.open_sidebar = False


if not "open_sidebar" in st.session_state:
    st.session_state.open_sidebar = False
if st.session_state.open_sidebar:
    new_title, new_question = generate_ticket(
        neo4j_graph=neo4j_graph,
        llm_chain=llm_chain,
        input_question=st.session_state[f"user_input"][-1],
    )
    with st.sidebar:
        st.title("Ticket draft")
        st.write("Auto generated draft ticket")
        st.text_input("Title", new_title)
        st.text_area("Description", new_question)
        st.button(
            "Submit to support team",
            type="primary",
            key="submit_ticket",
            on_click=close_sidebar,
        )


display_chat()
chat_input()
