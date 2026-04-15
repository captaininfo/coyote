import logging

log = logging.getLogger("coyote.agent")

class BaseLogger:
    def __init__(self) -> None:
        self.info = print


def extract_title_and_question(input_string):
    lines = input_string.strip().split("\n")

    title = ""
    question = ""
    is_question = False  # flag to know if we are inside a "Question" block

    for line in lines:
        if line.startswith("Title:"):
            title = line.split("Title: ", 1)[1].strip()
        elif line.startswith("Question:"):
            question = line.split("Question: ", 1)[1].strip()
            is_question = (
                True  # set the flag to True once we encounter a "Question:" line
            )
        elif is_question:
            # if the line does not start with "Question:" but we are inside a "Question" block,
            # then it is a continuation of the question
            question += "\n" + line.strip()

    return title, question


def create_vector_index(driver) -> None:
    """Create Neo4j vector indexes for Webpage and Annotation embedding
    properties. Safe to call repeatedly — uses IF NOT EXISTS.

    IMPORTANT: If EMBEDDING_DIMENSION changes, existing indexes must be
    dropped and recreated. IF NOT EXISTS will not update a malformed index.
    Run: DROP INDEX webpage_embedding; DROP INDEX annotation_embedding;
    Then restart the bot to recreate them.
    """
    from shared.embedding_config import EMBEDDING_MODEL_NAME, EMBEDDING_DIMENSION
    indexes = [
        ("webpage_embedding", "Webpage", "embedding"),
        ("annotation_embedding", "Annotation", "embedding"),
    ]
    for index_name, label, prop in indexes:
        query = (
            f"CREATE VECTOR INDEX {index_name} IF NOT EXISTS "
            f"FOR (n:{label}) ON n.{prop} "
            f"OPTIONS {{indexConfig: {{`vector.dimensions`: {EMBEDDING_DIMENSION}, "
            f"`vector.similarity_function`: 'cosine'}}}}"
        )
        try:
            driver.query(query)
            log.info("Vector index ensured: %s (model=%s, dim=%d)",
                     index_name, EMBEDDING_MODEL_NAME, EMBEDDING_DIMENSION)
        except Exception as e:
            log.warning("Vector index creation issue for %s: %s",
                        index_name, e)


def create_constraints(driver):
    """Create uniqueness constraints for Coyote schema nodes.

    Coyote schema:
    - Webpage: identified by url
    - Annotation: identified by annotation_id
    - Purpose: identified by event_id
    - SearchTerms: identified by event_id
    - WikiDataOntology: identified by uri
    """
    constraints = [
        ("webpage_url", "Webpage", "url"),
        ("annotation_id", "Annotation", "annotation_id"),
        ("purpose_event_id", "Purpose", "event_id"),
        ("searchterms_event_id", "SearchTerms", "event_id"),
        ("wikidata_uri", "WikiDataOntology", "uri"),
    ]
    for constraint_name, label, property_name in constraints:
        query = f"CREATE CONSTRAINT {constraint_name} IF NOT EXISTS FOR (n:{label}) REQUIRE (n.{property_name}) IS UNIQUE"
        try:
            driver.query(query)
        except Exception as e:
            log.debug("Constraint creation skipped for %s: %s", constraint_name, e)


def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)
