import logging
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

from langchain.prompts import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    SystemMessagePromptTemplate,
)

from typing import List, Any
from utils import BaseLogger, extract_title_and_question, format_docs
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from shared.nl2cypher import (
    strip_fences_or_json,
    looks_like_cypher,
    is_read_only,
    prompt_text,
    schema_for_prompts
)

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
            model_name="all-MiniLM-L6-v2", cache_folder="/embedding_model"
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
    You are a helpful assistant that helps a support agent with answering programming questions.
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


# images/agent/app/chains.py
# Complete hybrid implementation - replace the relevant sections

from typing import List, Dict, Any
import re
from langchain_core.runnables import RunnableParallel, RunnablePassthrough, RunnableLambda
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain.prompts import SystemMessagePromptTemplate, HumanMessagePromptTemplate
from langchain_neo4j import Neo4jGraph
import logging

# Import from shared nl2cypher
from shared.nl2cypher import (
    strip_fences_or_json,
    looks_like_cypher,
    is_read_only,
    prompt_text,
    schema_for_prompts
)

log = logging.getLogger("coyote.agent")

def configure_qa_rag_chain(llm, embeddings, embeddings_store_url, username, password):
    """
    Hybrid GraphRAG chain with intelligent fallback:
    TIER 1: Topic-based term matching (fast)
    TIER 2: LLM-generated Cypher (for analytical queries)
    TIER 3: Time-filtered fallback (for generic queries)
    """
    
    general_system_template = """
    You answer ONLY from the user's personal browsing corpus shown in CONTEXT.
    Rules (must follow):
    - If CONTEXT is just the single character "∅", reply exactly:
      "I couldn't find anything matching your query in the selected time window."
    - Do not invent items, categories, or URLs. Cite only URLs that appear in CONTEXT.
    - Never include <think> or reveal internal reasoning.
    - Keep it concise; use bullet points; group by Webpages vs Annotations when helpful.
    ----
    CONTEXT:
    {summaries}
    ----
    """
    general_user_template = "Question:```{question}```"
    qa_prompt = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(general_system_template),
        HumanMessagePromptTemplate.from_template(general_user_template),
    ])

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
        "search","searches","query","queries","purpose","purposes","term","terms",
        
        # Spelled numbers
        "one","two","three","four","five","six","seven","eight","nine","ten",
        "couple","few","several","many","dozen"
    }

    def _days_from_text(text: str) -> int:
        """Extract time window from natural language with spelled number support"""
        t = (text or "").lower()
        
        # Map spelled numbers to digits
        num_words = {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "couple": 2, "few": 3
        }
        
        # Try numeric pattern: "past 3 days"
        m = re.search(r"(past|last)\s+(\d+)\s+(day|week|month|year)s?", t)
        if m:
            n = int(m.group(2))
            unit = m.group(3)
            return {"day": 1, "week": 7, "month": 30, "year": 365}[unit] * n
        
        # Try spelled-out numbers: "past three weeks"
        for word, num in num_words.items():
            m = re.search(rf"(past|last)\s+{word}\s+(day|week|month|year)s?", t)
            if m:
                unit = m.group(2)
                return {"day": 1, "week": 7, "month": 30, "year": 365}[unit] * num
        
        # Common phrases
        if "today" in t: return 1
        if "yesterday" in t: return 2
        if "this week" in t or "past week" in t: return 7
        if "last week" in t: return 14
        if "this month" in t or "past month" in t: return 30
        if "last month" in t: return 30
        if "this year" in t or "past year" in t: return 365
        
        return 90  # default

    def _terms(q: str) -> list[str]:
        """Extract search terms, filtering stop words"""
        quoted = re.findall(r'[""](.+?)[""]', (q or "").lower())
        if quoted:
            return [t.strip() for t in quoted if len(t.strip()) >= 3][:6]
        words = re.findall(r"[a-z0-9\-]{3,}", (q or "").lower())
        return [w for w in words if w not in STOP][:6]

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
    OPTIONAL MATCH (w)-[:HAS_TOPIC]->(t:WikiDataOntology)
    WITH w, terms, collect(DISTINCT toLower(coalesce(t.label,''))) AS lbls
    WITH w, terms, [l IN lbls WHERE l <> ''] AS lbls2
    WHERE size(terms) = 0 OR any(term IN terms WHERE
             any(l IN lbls2 WHERE l CONTAINS term)
          OR toLower(coalesce(w.title,''))   CONTAINS term
          OR toLower(coalesce(w.summary,'')) CONTAINS term
          OR toLower(coalesce(w.topics,''))  CONTAINS term
    )
    WITH w, lbls2,
         (CASE WHEN size(lbls2) > 0
               THEN ' | topics: ' + reduce(s = '', x IN lbls2 | s + CASE WHEN s = '' THEN '' ELSE ', ' END + x)
               ELSE '' END) AS topics_part
    RETURN 'WEBPAGE: ' + coalesce(w.title,'(untitled)') + topics_part +
           ' | url: ' + coalesce(w.url,'') AS text,
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

    def _try_tier1_graphrag(days: int, terms: list[str]) -> tuple[bool, str]:
        """
        TIER 1: Fast topic/term matching
        Returns: (success, context_string)
        """
        try:
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
            return (False, "")

    def _try_tier2_cypher(question: str, days: int) -> tuple[bool, str]:
        """
        TIER 2: LLM-generated Cypher for analytical queries
        Returns: (success, context_string)
        """
        try:
            log.debug("TIER 2 (Cypher generation): attempting for analytical query")
            
            # Get schema and build prompt
            schema = graph.get_structured_schema
            if callable(schema):
                schema_str = str(schema)
            else:
                schema_str = str(schema) if schema else schema_for_prompts(None)
            
            # Use the PROMPT_TABLE from nl2cypher (better for analytical queries)
            prompt_template = prompt_text("table")
            full_prompt = prompt_template.format(schema=schema_str, question=question)
            
            # Generate Cypher from LLM
            from langchain_ollama import OllamaLLM
            import os
            
            llm_for_cypher = OllamaLLM(
                model=os.getenv("LLM", "qwen2.5-coder:7b"),
                base_url=os.getenv("OLLAMA_BASE_URL", "http://llm:11434"),
                temperature=0
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
            WITH w, collect(DISTINCT toLower(coalesce(t.label,''))) AS lbls
            WITH w, [l IN lbls WHERE l <> ''] AS lbls2,
                 (CASE WHEN size([l IN lbls WHERE l <> '']) > 0
                       THEN ' | topics: ' + reduce(s = '', x IN [l IN lbls WHERE l <> ''] | 
                            s + CASE WHEN s = '' THEN '' ELSE ', ' END + x)
                       ELSE '' END) AS topics_part
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

    def _build_context_hybrid(q: str) -> str:
        """
        Hybrid context builder with intelligent 3-tier fallback:
        TIER 1: Topic-based GraphRAG (fast, works for most queries)
        TIER 2: LLM-generated Cypher (for analytical queries)
        TIER 3: Time-filtered list (for generic "show all" queries)
        """
        days = _days_from_text(q)
        terms = _terms(q)
        
        # TIER 1: Try topic/term-based GraphRAG first (fastest)
        success, context = _try_tier1_graphrag(days, terms)
        if success:
            return context
        
        # TIER 1 found nothing. Decide next step based on query type.
        
        # If we had specific search terms, this was a topic query that genuinely found nothing
        if terms:
            log.debug("TIER 1 found nothing for specific terms=%s; returning ∅", terms)
            return "∅"
        
        # No search terms extracted. Check if this is an analytical query.
        if _should_try_cypher(q):
            # TIER 2: Try Cypher generation for analytical/aggregate queries
            success, context = _try_tier2_cypher(q, days)
            if success:
                return context
            # Cypher failed, continue to TIER 3
        
        # TIER 3: Fall back to time-filtered list
        return _try_tier3_fallback(days)

    # Build the chain
    summaries_fn = RunnableLambda(lambda q: _build_context_hybrid(q))

    chain = (
        RunnableParallel({
            "summaries": summaries_fn,
            "question": RunnablePassthrough(),
        })
        | qa_prompt
        | llm
        | StrOutputParser()
    )
    
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



def generate_ticket(neo4j_graph, llm_chain, input_question):
    """
    Remove dependency on (q:Question) / StackOverflow demo.
    Minimal, deterministic ticket draft: title = first line (trimmed),
    description = original user input.
    """
    if not input_question:
        return ("Coyote support request", "No description provided.")
    first = input_question.strip().splitlines()[0].strip()
    title = (first[:80] + "…") if len(first) > 80 else first
    return (title or "Coyote support request", input_question.strip())