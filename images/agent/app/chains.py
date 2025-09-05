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


def configure_qa_rag_chain(llm, embeddings, embeddings_store_url, username, password):
    """
    Browsing‑corpus GraphRAG (graph‑only retriever).
    Hardened against label=list vs string type mismatches.
    """
    import re
    from langchain_core.runnables import RunnableParallel, RunnablePassthrough, RunnableLambda
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate
    from langchain.prompts import SystemMessagePromptTemplate, HumanMessagePromptTemplate
    from langchain_neo4j import Neo4jGraph
    import logging

    log = logging.getLogger("coyote.agent")

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

    # --- small helper: infer a day window from natural language (today, last N days, etc.)
    def _days_from_text(text: str) -> int:
        t = (text or "").lower()
        m = re.search(r"(past|last)\s+(\d+)\s+(day|week|month|year)s?", t)
        if m:
            n = int(m.group(2))
            unit = m.group(3)
            return {"day": 1, "week": 7, "month": 30, "year": 365}[unit] * n
        if "today" in t: return 1
        if "yesterday" in t: return 2
        if "this week" in t or "past week" in t: return 7
        if "last week" in t: return 7
        if "this month" in t or "past month" in t: return 30
        if "last month" in t: return 30
        if "this year" in t or "past year" in t: return 365
        return 90  # sensible default for browsing topics

    # --- Extract terms: prefer quoted spans, else de-stopworded keywords (<=6 terms)
    STOP = {"the","a","an","and","or","what","which","have","i","about","in","of","on","for",
            "to","this","that","past","last","week","weeks","month","months","year","years",
            "today","yesterday","recent","recently","my","me","did","do"}
    def _terms(q: str) -> list[str]:
        quoted = re.findall(r'["“](.+?)["”]', (q or "").lower())
        if quoted:
            return [t.strip() for t in quoted if len(t.strip()) >= 3][:6]
        words = re.findall(r"[a-z0-9\-]{3,}", (q or "").lower())
        return [w for w in words if w not in STOP][:6]

    # TOPIC/WEBPAGE lines — APOC‑free and resilient when HAS_TOPIC edges are missing.
    CY_TOPICS_SAFE = """
    WITH $terms AS terms, $days AS days
    MATCH (w:Webpage)
    WHERE w.timestamp IS NOT NULL
      AND (days IS NULL OR datetime(w.timestamp) >= datetime() - duration({days: days}))
    // include topics if present
    OPTIONAL MATCH (w)-[:HAS_TOPIC]->(t:WikiDataOntology)
    WITH w, terms, collect(DISTINCT toLower(coalesce(t.label,''))) AS lbls
    WITH w, terms, [l IN lbls WHERE l <> ''] AS lbls2
    // keep when ANY term matches either topic labels OR title/summary/topics property text
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

    # ANNOTATION lines — APOC‑free and uses extracted terms
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

    def _build_context(q: str) -> str:
        days  = _days_from_text(q)
        terms = _terms(q)
        try:
            params = {"terms": terms, "days": days}
            rows1 = graph.query(CY_TOPICS_SAFE, params) or []
            rows2 = graph.query(CY_TEXT_SAFE,   params) or []
            log.debug(
                "GraphRAG: days=%s terms=%s; CY_TOPICS rows=%d; CY_TEXT rows=%d",
                days, terms, len(rows1), len(rows2)
            )
            lines = [r.get("text","") for r in rows1] + [r.get("text","") for r in rows2]
            ctx = "\n\n".join(x for x in lines if x)
            return ctx if ctx else "∅"
        except Exception:
            log.exception("GraphRAG context retrieval failed (q=%r, days=%s terms=%s)", q, days, terms)
            return "∅"

    summaries_fn = RunnableLambda(lambda q: _build_context(q))

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
    Remove dependency on (q:Question) / StackOverflow demo.
    Minimal, deterministic ticket draft: title = first line (trimmed),
    description = original user input.
    """
    if not input_question:
        return ("Coyote support request", "No description provided.")
    first = input_question.strip().splitlines()[0].strip()
    title = (first[:80] + "…") if len(first) > 80 else first
    return (title or "Coyote support request", input_question.strip())