import os
import streamlit as st
from streamlit.logger import get_logger
from langchain_core.callbacks import BaseCallbackHandler
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


load_dotenv(".env")

url = os.getenv("NEO4J_URI")
username = os.getenv("NEO4J_USERNAME")
password = os.getenv("NEO4J_PASSWORD")
ollama_base_url = os.getenv("OLLAMA_BASE_URL")
embedding_model_name = os.getenv("EMBEDDING_MODEL")
llm_name = os.getenv("LLM")
# Remapping for Langchain Neo4j integration
os.environ["NEO4J_URL"] = url

logger = get_logger(__name__)

# if Neo4j is local, you can go to http://localhost:7474/ to browse the database
neo4j_graph = Neo4jGraph(
    url=url, username=username, password=password, refresh_schema=False
)


schema = get_schema(url, username, password)

embeddings, dimension = load_embedding_model(
    embedding_model_name, config={"ollama_base_url": ollama_base_url}, logger=logger
)
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
1. Use ONLY the labels, properties, and relationships listed above
2. Topics and entities are linked via WikiDataOntology nodes
3. To find content about specific topics, use pattern: (:Webpage)-[:HAS_TOPIC]->(:WikiDataOntology)
4. WikiDataOntology nodes have 'label' property containing topic names
5. Use case-insensitive matching: toLower(property) CONTAINS toLower('search_term')
6. Return counts with: count(DISTINCT node) AS descriptiveName
7. For date filtering, use: datetime(w.timestamp) >= datetime() - duration({{days: 30}})

COMMON PATTERNS:
- Articles/webpages about topic X: MATCH (w:Webpage)-[:HAS_TOPIC]->(t:WikiDataOntology) WHERE toLower(t.label) CONTAINS toLower('X')
- Annotations about topic X: MATCH (a:Annotation)-[:HAS_TOPIC]->(t:WikiDataOntology) WHERE toLower(t.label) CONTAINS toLower('X')
- Recent content: WHERE datetime(node.timestamp) >= datetime() - duration({{days: N}})
- "how many": Use count(DISTINCT node) AS numberOfResults

OUTPUT: Return ONLY valid JSON: {{"cypher": "YOUR_QUERY_HERE"}}

USER QUESTION: {question}
"""

# Initialize the cypher chain
cypher_chain = GraphCypherQAChain.from_llm(
    llm=llm,
    graph=neo4j_graph,
    cypher_prompt=ChatPromptTemplate.from_template(CUSTOM_PROMPT).partial(schema=schema_text),
    verbose=True,
    allow_dangerous_requests=True,
    return_intermediate_steps=True,
    top_k=10  # Return more results by default
)



# Streamlit UI
styl = f"""
<style>
    /* not great support for :has yet (hello FireFox), but using it for now */
    .element-container:has([aria-label="Select RAG mode"]) {{
      position: fixed;
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

    if user_input:
        with st.chat_message("user"):
            st.write(user_input)
        with st.chat_message("assistant"):
            st.caption(f"RAG: {name}")
            stream_handler = StreamHandler(st.empty())

            # The snippet below is for testing and should be deleted
            if name == "Cypher-only":
                resp = cypher_chain.invoke(
                    user_input,
                    config={"callbacks": [stream_handler]},
                    return_intermediate_steps=True,
                )
                # `intermediate_steps` is a list of dicts; grab the first one
                steps = resp.get("intermediate_steps", [])
                if steps and isinstance(steps[0], dict) and "cypher_query" in steps[0]:
                    logger.info("Generated Cypher:\n%s", steps[0]["cypher_query"])
                else:
                    logger.warning("No cypher_query found in intermediate_steps: %s", steps)

                # Delete above

            output = output_function.invoke(
                user_input, config={"callbacks": [stream_handler]}
            )

            st.session_state[f"user_input"].append(user_input)
            st.session_state[f"generated"].append(output)
            st.session_state[f"rag_mode"].append(name)


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
    options = ["Disabled", "Enabled", "Cypher-only"]
    return st.radio("Select RAG mode", options, horizontal=True)


name = mode_select()
if name == "Disabled":
    output_function = llm_chain          # plain LLM
elif name == "Enabled":
    output_function = rag_chain          # vector + graph RAG
elif name == "Cypher-only":              # add a radio option if you like
    output_function = cypher_chain



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
