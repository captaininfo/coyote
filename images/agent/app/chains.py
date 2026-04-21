import logging
import re
import os
from langchain_openai import OpenAIEmbeddings
from langchain_ollama import OllamaEmbeddings
from langchain_aws import BedrockEmbeddings
from langchain_huggingface import HuggingFaceEmbeddings

from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama
from langchain_aws import ChatBedrock

from langchain_neo4j import Neo4jGraph

from langchain_core.runnables import RunnableParallel, RunnablePassthrough, RunnableLambda
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    SystemMessagePromptTemplate,
)

from typing import List, Dict, Any
from utils import BaseLogger, extract_title_and_question, format_docs
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from shared.nl2cypher import (
    strip_fences_or_json,
    looks_like_cypher,
    is_read_only,
    prompt_text,
    schema_for_prompts
)
from shared.time_utils import days_from_text

log = logging.getLogger("coyote.agent")

AWS_MODELS = (
    "ai21.jamba-instruct-v1:0",
    "amazon.titan",
    "anthropic.claude",
    "cohere.command",
    "meta.llama",
    "mistral.mi",
)


def load_embedding_model(embedding_model_name: str, logger=BaseLogger(), config={}):
    if embedding_model_name == "ollama":
        embeddings = OllamaEmbeddings(
            base_url=config["ollama_base_url"], model="llama2"
        )
        dimension = 4096
        logger.info("Embedding: Using Ollama")
    elif embedding_model_name == "openai":
        embeddings = OpenAIEmbeddings()
        dimension = 1536
        logger.info("Embedding: Using OpenAI")
    elif embedding_model_name == "aws":
        embeddings = BedrockEmbeddings()
        dimension = 1536
        logger.info("Embedding: Using AWS")
    elif embedding_model_name == "google-genai-embedding-001":
        embeddings = GoogleGenerativeAIEmbeddings(model="models/embedding-001")
        dimension = 768
        logger.info("Embedding: Using Google Generative AI Embeddings")
    else:
        embeddings = HuggingFaceEmbeddings(
            model_name="all-MiniLM-L6-v2"
        )
        dimension = 384
        logger.info("Embedding: Using SentenceTransformer")
    log.debug("Embeddings loaded: name=%s dim=%s", embedding_model_name, dimension)
    return embeddings, dimension


def load_llm(llm_name: str, logger=BaseLogger(), config={}):
    if llm_name in ["gpt-4", "gpt-4o", "gpt-4-turbo"]:
        logger.info("LLM: Using GPT-4")
        return ChatOpenAI(temperature=0, model_name=llm_name, streaming=True)
    elif llm_name == "gpt-3.5":
        logger.info("LLM: Using GPT-3.5")
        return ChatOpenAI(temperature=0, model_name="gpt-3.5-turbo", streaming=True)
    elif llm_name == "claudev2":
        logger.info("LLM: ClaudeV2")
        return ChatBedrock(
            model_id="anthropic.claude-v2",
            model_kwargs={"temperature": 0.0, "max_tokens_to_sample": 1024},
            streaming=True,
        )
    elif llm_name.startswith(AWS_MODELS):
        logger.info(f"LLM: {llm_name}")
        return ChatBedrock(
            model_id=llm_name,
            model_kwargs={"temperature": 0.0, "max_tokens_to_sample": 1024},
            streaming=True,
        )

    elif len(llm_name):
        logger.info(f"LLM: Using Ollama: {llm_name}")
        return ChatOllama(
            temperature=0,
            base_url=config["ollama_base_url"],
            model=llm_name,
            streaming=True,
            # seed=2,
            top_k=10,  # A higher value (100) will give more diverse answers, while a lower value (10) will be more conservative.
            top_p=0.3,  # Higher value (0.95) will lead to more diverse text, while a lower value (0.5) will generate more focused text.
            num_ctx=3072,  # Sets the size of the context window used to generate the next token.
        )
    logger.info("LLM: Using GPT-3.5")
    log.debug("LLM instantiated: %s", llm_name)
    return ChatOpenAI(temperature=0, model_name="gpt-3.5-turbo", streaming=True)


def configure_llm_only_chain(llm):
    # LLM only response
    template = """
    You are a personal research assistant for the Coyote learning system.
    You help users reflect on their browsing and learning activity.
    If you don't know the answer, just say that you don't know, you must not make up an answer.
    """
    system_message_prompt = SystemMessagePromptTemplate.from_template(template)
    human_template = "{question}"
    human_message_prompt = HumanMessagePromptTemplate.from_template(human_template)
    chat_prompt = ChatPromptTemplate.from_messages(
        [system_message_prompt, human_message_prompt]
    )
    chain = chat_prompt | llm | StrOutputParser()
    return chain


def configure_qa_rag_chain(llm, embeddings, embeddings_store_url, username, password):
    """
    Hybrid GraphRAG chain with intelligent fallback:
    TIER 0: Pure cosine vector search over webpage_embedding and
            annotation_embedding indexes (Phase C v1). Runs after the
            search-intent branch, before Tier 1.
    TIER 1: Topic-based term matching (fast)
    TIER 2: LLM-generated Cypher (for analytical queries)
    TIER 3: Time-filtered fallback (for generic queries)
    """
    
    general_system_template = """You are a personal research assistant analyzing browsing history data.

    CRITICAL INSTRUCTION - READ CAREFULLY:
    You MUST answer questions using ONLY the information provided in the CONTEXT section below.
    The CONTEXT contains real data from the user's browsing history.
    DO NOT say you don't have access to data - the data IS in CONTEXT.
    DO NOT use your training knowledge - ONLY use what's in CONTEXT.

    If CONTEXT is the single character "∅", respond: "I couldn't find anything matching your query in the selected time window."

    Otherwise, answer the question using the webpages and annotations shown in CONTEXT.
    Cite URLs when providing information.
    
    CONTEXT (this is real data you MUST use):
    {summaries}

    Now answer the user's question using ONLY the CONTEXT above.
    """
    general_user_template = "Question: {question}"
    qa_prompt = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(general_system_template),
        HumanMessagePromptTemplate.from_template(general_user_template),
    ])

    # Verify prompt template works
    try:
        qa_prompt.format_messages(summaries="TEST", question="test")
        log.debug("Prompt template validated")
    except Exception as e:
        log.error("Prompt template expansion failed: %s", e)

    # Tier labels for transparency — prepended to context so the LLM knows the source
    TIER_LABELS = {
        "0-vector": "[Source: Semantic vector search over your browsing data]",
        "1-topics": "[Source: Topic/keyword match from your browsing data]",
        "1-searches": "[Source: Your recent search history]",
        "2-cypher": "[Source: Analytical query over your browsing data]",
        "3-fallback": "[Source: Recent browsing activity (no specific match found)]",
    }

    graph = Neo4jGraph(
        url=embeddings_store_url, username=username, password=password, refresh_schema=False
    )

    # ============================================================================
    # HELPER FUNCTIONS
    # ============================================================================

    # Expanded STOP word list
    STOP = {
        # Core stop words
        "the","a","an","and","or","what","which","have","i","about","in","of","on","for",
        "to","this","that","past","last","week","weeks","month","months","year","years",
        "today","yesterday","recent","recently","my","me","did","do","from","with",
        
        # Structural/meta terms
        "webpage","webpages","page","pages","article","articles","site","sites","website","websites",
        "annotation","annotations","note","notes","highlight","highlights",
        "viewed","visited","browsed","read","saw","looked","seen","shown","show",
        "search","searched", "searches","query","queries","purpose","purposes","term","terms",
        "topic","topics","popular","most",
        
        # Spelled numbers
        "one","two","three","four","five","six","seven","eight","nine","ten",
        "couple","few","several","many","dozen"
    }

    def _terms(q: str) -> list[str]:
        """Extract search terms, filtering stop words"""
        quoted = re.findall(r'[""](.+?)[""]', (q or "").lower())
        if quoted:
            return [t.strip() for t in quoted if len(t.strip()) >= 3][:6]
        words = re.findall(r"[a-z0-9\-]{3,}", (q or "").lower())
        filtered = [w for w in words if w not in STOP]
        # Expand hyphenated terms: "vibe-coding" also yields "vibe coding"
        expanded = []
        for w in filtered:
            expanded.append(w)
            if "-" in w:
                expanded.append(w.replace("-", " "))
        return expanded[:6]

    def _should_try_cypher(question: str) -> bool:
        """Detect if question needs Cypher generation (analytical queries)"""
        q = question.lower()
        analytical_patterns = [
            r'how many',
            r'\bcount\b',
            r'number of',
            r'how much',
            r'compare',
            r'more than',
            r'less than',
            r'greater than',
            r'average',
            r'total',
            r'\bmost\b',
            r'\bleast\b',
            r'oldest',
            r'newest',
            r'first',
            r'last \d+',  # "last 5 pages" (specific number)
        ]
        return any(re.search(p, q) for p in analytical_patterns)

    # TIER 1: Topic/term-based GraphRAG queries
    CY_TOPICS_SAFE = """
    WITH $terms AS terms, $days AS days
    MATCH (w:Webpage)
    WHERE w.timestamp IS NOT NULL
      AND (days IS NULL OR datetime(w.timestamp) >= datetime() - duration({days: days}))
    // Parse topics JSON → rows, filter, order, slice
    WITH w, terms, apoc.convert.fromJsonList(coalesce(w.topics,'[]')) AS topic_items
    UNWIND topic_items AS ti
    WITH w, terms,
         toLower(coalesce(ti.label, ti.topic, '')) AS lab,
         coalesce(ti.score, 0.0) AS sc
    WHERE lab <> '' AND size(lab) > 2 AND size(lab) < 50
      AND NOT lab STARTS WITH 'category:' AND NOT lab STARTS WITH 'q'
    ORDER BY sc DESC
    WITH w, terms, collect({label: lab, score: sc})[0..5] AS top_topics
    WHERE size(terms) = 0 OR any(term IN terms WHERE
             any(t IN top_topics WHERE t.label CONTAINS term)
          OR toLower(coalesce(w.title,''))   CONTAINS term
          OR toLower(coalesce(w.summary,'')) CONTAINS term
          OR toLower(coalesce(w.url,''))     CONTAINS term)
    WITH w, top_topics,
         CASE WHEN size(top_topics) > 0
              THEN ' | topics: ' + reduce(s = '', t IN top_topics |
                   s + CASE WHEN s = '' THEN '' ELSE ', ' END + t.label)
              ELSE '' END AS topics_part
    RETURN 'WEBPAGE: ' + coalesce(w.title, w.url, '(no title)') + topics_part +
           ' | url: ' + coalesce(w.url, '') AS text,
           datetime(w.timestamp) AS ts
    ORDER BY ts DESC
    LIMIT 12
    """

    CY_TEXT_SAFE = """
    WITH $terms AS terms, $days AS days
    MATCH (a:Annotation)<-[:HAS_ANNOTATION]-(w:Webpage)
    WHERE a.timestamp IS NOT NULL
      AND (days IS NULL OR datetime(a.timestamp) >= datetime() - duration({days: days}))
      AND (size(terms) = 0 OR any(term IN terms WHERE
             toLower(coalesce(a.annotation_text,'')) CONTAINS term
          OR toLower(coalesce(w.title,''))          CONTAINS term))
    RETURN 'ANNOTATION: ' + coalesce(a.annotation_text,'') +
           ' | page: ' + coalesce(w.title,'(untitled)') +
           ' | url: '  + coalesce(w.url,'') AS text,
           datetime(a.timestamp) AS ts
    ORDER BY ts DESC
    LIMIT 12
    """

    CY_SEARCHES_SAFE = """
    WITH $days AS days
    MATCH (p:Purpose)-[:INITIATES_SEARCH]->(s:SearchTerms)
    WHERE p.timestamp IS NOT NULL
      AND (days IS NULL OR datetime(p.timestamp) >= datetime() - duration({days: days}))
    OPTIONAL MATCH (p)-[:HAS_TOPIC]->(t:WikiDataOntology)
    WITH s, p, [x IN collect(DISTINCT toLower(coalesce(t.label,''))) WHERE x <> ''] AS lbls2
    WITH s, p, lbls2,
         CASE WHEN size(lbls2) > 0
              THEN ' | topics: ' + reduce(s1 = '', x IN lbls2 |
                   s1 + CASE WHEN s1 = '' THEN '' ELSE ', ' END + x)
              ELSE '' END AS topics_part
    RETURN 'SEARCH: ' + coalesce(s.text,'') + topics_part +
           ' | ts: ' + toString(datetime(p.timestamp)) AS text,
           datetime(p.timestamp) AS ts
    ORDER BY ts DESC
    LIMIT 12
    """

    # TIER 0: Pure cosine vector search (Phase C v1)
    CY_VECTOR_WEBPAGES = """
    CALL db.index.vector.queryNodes('webpage_embedding', $top_k, $query_vector)
    YIELD node AS w, score
    WHERE score >= $threshold
      AND w.timestamp IS NOT NULL
      AND datetime(w.timestamp) >= datetime() - duration({days: $days})
    RETURN 'WEBPAGE [input]: ' + coalesce(w.title, w.url, '(no title)') +
           ' | url: ' + coalesce(w.url, '') +
           ' | score: ' + toString(round(score * 100) / 100) AS text,
           score
    ORDER BY score DESC
    """

    CY_VECTOR_ANNOTATIONS = """
    CALL db.index.vector.queryNodes('annotation_embedding', $top_k, $query_vector)
    YIELD node AS a, score
    WHERE score >= $threshold
      AND a.timestamp IS NOT NULL
      AND datetime(a.timestamp) >= datetime() - duration({days: $days})
    OPTIONAL MATCH (w:Webpage)-[:HAS_ANNOTATION]->(a)
    RETURN 'ANNOTATION [output]: ' + coalesce(a.annotation_text, '') +
           ' | page: ' + coalesce(w.title, '(untitled)') +
           ' | url: ' + coalesce(w.url, '') +
           ' | score: ' + toString(round(score * 100) / 100) AS text,
           score
    ORDER BY score DESC
    """

    def _try_tier0_vector(question: str, days: int) -> tuple[bool, str]:
        """
        TIER 0: Pure cosine vector search over webpage_embedding and
        annotation_embedding indexes. No relationship traversal.
        Returns: (True, ctx) | (False, "") | (None, "") per 3-state convention.
        Role labels [input]/[output] are embedded in result text to preserve
        the input/output separation invariant (see CLAUDE.md Design Vision).
        """
        try:
            threshold = float(os.getenv("VECTOR_SIMILARITY_THRESHOLD", "0.65"))
            try:
                query_vector = embeddings.embed_query(question)
            except Exception:
                log.exception("TIER 0: query embedding failed")
                return (None, "")

            params = {
                "query_vector": query_vector,
                "threshold": threshold,
                "days": days,
                "top_k": 10,
            }

            w_rows = graph.query(CY_VECTOR_WEBPAGES, params) or []
            a_rows = graph.query(CY_VECTOR_ANNOTATIONS, params) or []

            lines = [r.get("text", "") for r in w_rows] + [r.get("text", "") for r in a_rows]
            ctx = "\n\n".join(x for x in lines if x)

            if not ctx:
                log.debug("TIER 0: no vector hits above threshold %.2f (days=%d)", threshold, days)
                return (False, "")

            log.debug("TIER 0: %d webpage hits, %d annotation hits above threshold %.2f",
                      len(w_rows), len(a_rows), threshold)
            return (True, ctx)

        except Exception:
            log.exception("TIER 0 (vector) failed")
            return (None, "")

    def _try_tier1_searches(days: int) -> tuple[bool, str]:
        rows = graph.query(CY_SEARCHES_SAFE, {"days": days}) or []
        lines = [r.get("text","") for r in rows]
        ctx = "\n\n".join(x for x in lines if x)
        return (bool(ctx), ctx if ctx else "")

    def _try_tier1_graphrag(days: int, terms: list[str]) -> tuple[bool, str]:
        """
        TIER 1: Fast topic/term matching
        Returns: (success, context_string)
        """
        try:
            if not terms:
                return (False, "")
            params = {"terms": terms, "days": days}
            rows1 = graph.query(CY_TOPICS_SAFE, params) or []
            rows2 = graph.query(CY_TEXT_SAFE, params) or []
            
            if rows1 or rows2:
                log.debug("TIER 1 (GraphRAG): days=%s terms=%s; found %d results",
                         days, terms, len(rows1) + len(rows2))
                lines = [r.get("text","") for r in rows1] + [r.get("text","") for r in rows2]
                ctx = "\n\n".join(x for x in lines if x)
                return (True, ctx if ctx else "∅")
            
            log.debug("TIER 1 (GraphRAG): no results for terms=%s days=%d", terms, days)
            return (False, "")
            
        except Exception:
            log.exception("TIER 1 (GraphRAG) failed")
            # Signal orchestrator that this was an error (not an empty hit)
            return (None, "")

    def _try_tier2_cypher(question: str, days: int) -> tuple[bool, str]:
        """
        TIER 2: LLM-generated Cypher for analytical queries
        Returns: (success, context_string)
        """
        try:
            log.debug("TIER 2 (Cypher generation): attempting for analytical query")
            
            # Get text schema the same way the bot does for NL→Cypher
            from coyote_schema import get_schema
            schema_str = get_schema(url=os.getenv("NEO4J_URI"), username=os.getenv("NEO4J_USERNAME"), password=os.getenv("NEO4J_PASSWORD"))
            prompt_template = prompt_text("table")  # shared prompt
            full_prompt = prompt_template.format(schema=schema_for_prompts(schema_str), question=question)
            
            # Generate Cypher from LLM
            from langchain_ollama import OllamaLLM
            import os
            
            llm_for_cypher = OllamaLLM(
                model=os.getenv("LLM", "qwen2.5-coder:3b"),
                base_url=os.getenv("OLLAMA_BASE_URL", "http://llm:11434"),
                temperature=0,
                num_predict=256,
            )
            
            raw_response = llm_for_cypher.invoke(full_prompt)
            cypher = strip_fences_or_json(raw_response or "")
            
            log.debug("TIER 2: Generated Cypher: %s", cypher[:200])
            
            # Validate and execute
            if not looks_like_cypher(cypher):
                log.debug("TIER 2: Output doesn't look like Cypher")
                return (False, "")
            
            if not is_read_only(cypher):
                log.warning("TIER 2: Generated write query, blocked")
                return (False, "")
            
            # Execute the generated Cypher
            rows = graph.query(cypher) or []
            
            if not rows:
                log.debug("TIER 2: Cypher executed but returned no results")
                return (False, "")
            
            # Format results
            if len(rows) == 1 and "numberOfResults" in rows[0]:
                result = f"**Result:** {rows[0]['numberOfResults']}"
            else:
                # Format as compact list
                lines = []
                for r in rows[:20]:
                    if isinstance(r, dict):
                        # Format dict as "key: value" pairs
                        line = " | ".join(f"{k}: {v}" for k, v in r.items() if v is not None)
                        lines.append(line)
                result = "\n".join(lines)
            
            log.debug("TIER 2: Success, returning %d rows", len(rows))
            return (True, result)
            
        except Exception:
            log.exception("TIER 2 (Cypher generation) failed")
            return (False, "")

    def _try_tier3_fallback(days: int) -> str:
        """
        TIER 3: Time-filtered fallback (show all pages in window)
        Returns: context_string
        """
        try:
            log.debug("TIER 3 (time-filtered fallback): days=%d", days)
            
            fallback_query = """
            MATCH (w:Webpage)
            WHERE w.timestamp IS NOT NULL
              AND datetime(w.timestamp) >= datetime() - duration({days: $days})
            OPTIONAL MATCH (w)-[:HAS_TOPIC]->(t:WikiDataOntology)
            WITH w, [x IN collect(DISTINCT toLower(coalesce(t.label,''))) WHERE x <> ''] AS lbls2
            WITH w, lbls2,
                 CASE WHEN size(lbls2) > 0
                      THEN ' | topics: ' + reduce(s = '', x IN lbls2 |
                           s + CASE WHEN s = '' THEN '' ELSE ', ' END + x)
                      ELSE '' END AS topics_part
            RETURN 'WEBPAGE: ' + coalesce(w.title,'(untitled)') + topics_part +
                   ' | url: ' + coalesce(w.url,'') AS text,
                   datetime(w.timestamp) AS ts
            ORDER BY ts DESC
            LIMIT 20
            """
            
            rows = graph.query(fallback_query, {"days": days}) or []
            
            if rows:
                log.debug("TIER 3: Found %d pages in time window", len(rows))
                lines = [r.get("text","") for r in rows]
                return "\n\n".join(x for x in lines if x)
            
            log.debug("TIER 3: No pages found in time window")
            return f"No browsing activity found in the last {days} days."
            
        except Exception:
            log.exception("TIER 3 (fallback) failed")
            return "∅"

    def _build_context_hybrid(q: str) -> tuple[str, str]:
        """
        Hybrid context builder with intelligent 3-tier fallback.
        Returns (context_str, tier_key) so callers know the source quality.
        """
        days = days_from_text(q)
        terms = _terms(q)

        log.info("Parsed query: days=%d, terms=%s", days, terms)

        # If user asks about searches, show recent searches first.
        # Note: SearchTerms/Purpose nodes are not embedded (see CLAUDE.md Known Issues),
        # so Tier 0 cannot serve this intent — route to Tier-1 searches directly.
        if re.search(r"\bsearch(ed|es|ing)?\b", q.lower()):
            ok, c = _try_tier1_searches(days)
            if ok:
                log.debug("Answered via TIER-1 (searches)")
                return (c, "1-searches")

        # TIER 0: Pure vector search (Phase C v1)
        success, context = _try_tier0_vector(q, days)
        if success:
            log.info("TIER 0 context: %d chars", len(context))
            return (context, "0-vector")
        if success is None:
            log.debug("Tier-0 errored; falling through to Tier-1")
            # fall through — do NOT return here

        # TIER 1: Try topic/term-based GraphRAG first (fastest)
        success, context = _try_tier1_graphrag(days, terms)
        if success:
            log.info("TIER 1 context: %d chars", len(context))
            return (context, "1-topics")

        # If Tier-1 errored, fall back to Tier-3
        if success is None:
            log.debug("Tier-1 errored; falling back to Tier-3")
            return (_try_tier3_fallback(days), "3-fallback")

        # Specific search terms found nothing
        if terms:
            log.debug("TIER 1 found nothing for terms=%s", terms)
            return ("∅", "1-topics")

        # No search terms extracted. Check if this is an analytical query.
        if _should_try_cypher(q):
            success, context = _try_tier2_cypher(q, days)
            if success:
                log.info("TIER 2 context: %d chars", len(context))
                return (context, "2-cypher")

        # TIER 3: Fall back to time-filtered list
        fallback_context = _try_tier3_fallback(days)
        log.info("TIER 3 context: %d chars", len(fallback_context))
        return (fallback_context, "3-fallback")

    # Build the chain
    def _context_with_tier(q: str) -> str:
        ctx, tier_key = _build_context_hybrid(q)
        label = TIER_LABELS.get(tier_key, "")
        return f"{label}\n\n{ctx}" if label else ctx

    summaries_fn = RunnableLambda(_context_with_tier)

    chain = (
        RunnableParallel({
            "summaries": summaries_fn,
            "question": RunnablePassthrough(),
        })
        | qa_prompt
        | llm
        | StrOutputParser()
    )

    log.info("RAG chain constructed")
    return chain


def generate_ticket(neo4j_graph, llm_chain, input_question):
    """
    Minimal, deterministic ticket draft for support requests
    """
    if not input_question:
        return ("Coyote support request", "No description provided.")
    first = input_question.strip().splitlines()[0].strip()
    title = (first[:80] + "…") if len(first) > 80 else first
    return (title or "Coyote support request", input_question.strip())

