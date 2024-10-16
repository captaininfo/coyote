import json
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def process_coyote_browser_extension_data(session, entry, state):
    purpose_id = None
    search_terms_id = None

    if entry.get("event") == "User starts or modifies a search":
        # Extract and set timestamp and dataSource at the start
        timestamp = entry.get("timestamp")
        dataSource = entry.get("dataSource", "Coyote Browser Extension")
        purpose_text = entry.get("purpose", "No Purpose")
        purpose_topics = json.dumps(entry.get("purposeTopics", []))
        purpose_entities = json.dumps(entry.get("purposeEntities", []))
        search_terms = entry.get("searchTerms", "No Search Terms")
        search_terms_topics = json.dumps(entry.get("searchTermsTopics", []))
        search_terms_entities = json.dumps(entry.get("searchTermsEntities", []))
        search_terms_relevance = json.dumps(entry.get("searchTermsRelevance", []))

        logging.info(f"Inserting Purpose and SearchTerms with timestamp: {timestamp}")
        result = session.execute_write(
            _create_purpose_and_search_terms,
            purpose_text, purpose_topics, purpose_entities, search_terms, search_terms_topics, search_terms_entities, search_terms_relevance, timestamp, dataSource
        )
        if result:
            purpose_id, search_terms_id = result

        state['last_search_terms_node_id'] = search_terms_id

    elif entry.get("event") == "Webpage loads" and state.get('last_search_terms_node_id'):
        # Extract and set timestamp and dataSource at the start
        timestamp = entry.get("timestamp")
        dataSource = entry.get("dataSource", "Coyote Browser Extension")  # Ensure dataSource is set here
        url = entry.get("url", "No URL")
        title = entry.get("webpageTitle", "No Title")
        summary = entry.get("webpageSummary", "No Summary")
        topics = json.dumps(entry.get("webpageTopics", []))
        entities = json.dumps(entry.get("webpageNamedEntities", []))
        is_serp = "- Google Search" in title or url.startswith("https://www.google.com/search?")

        logging.info(f"Inserting Webpage with URL: {url} at timestamp: {timestamp}")
        webpage_id = session.execute_write(
            _create_and_link_webpage,
            state['last_webpage_node_id'], state['last_search_terms_node_id'], url, title, summary, topics, entities, is_serp, timestamp, dataSource
        )
        logging.info(f"Webpage node created with ID: {webpage_id}")
        state['last_webpage_node_id'] = webpage_id

    return purpose_id, search_terms_id


def _create_purpose_and_search_terms(tx, purpose_text, purpose_topics, purpose_entities, search_terms, search_terms_topics, search_terms_entities, search_terms_relevance, timestamp, dataSource):
    query = """
    CREATE (p:Purpose {text: $purpose_text, topics: $purpose_topics, entities: $purpose_entities, timestamp: $timestamp, dataSource: $dataSource, isInput: false})
    CREATE (st:SearchTerms {text: $search_terms, topics: $search_terms_topics, entities: $search_terms_entities, relevance: $search_terms_relevance, timestamp: $timestamp, dataSource: $dataSource, isInput: false})
    CREATE (p)-[:INITIATES_SEARCH]->(st)
    RETURN id(p) AS purpose_id, id(st) AS search_terms_id
    """
    result = tx.run(query, purpose_text=purpose_text, purpose_topics=purpose_topics, purpose_entities=purpose_entities, 
                    search_terms=search_terms, search_terms_topics=search_terms_topics, 
                    search_terms_entities=search_terms_entities, search_terms_relevance=search_terms_relevance, 
                    timestamp=timestamp, dataSource=dataSource).single()
    return result["purpose_id"], result["search_terms_id"]


def _create_and_link_webpage(tx, last_webpage_node_id, last_search_terms_node_id, url, title, summary, topics, entities, is_serp, timestamp, dataSource):
    if is_serp or last_webpage_node_id is None:
        target_node_id = last_search_terms_node_id
        rel_type = 'GENERATES_SERP'
    else:
        target_node_id = last_webpage_node_id
        rel_type = 'LINKS_TO'

    query = f"""
    MATCH (node) WHERE id(node) = $node_id
    CREATE (w:Webpage {{url: $url, title: $title, summary: $summary, topics: $topics, entities: $entities, isSERP: $is_serp, timestamp: $timestamp, dataSource: $dataSource, isInput: true}})
    CREATE (node)-[:{rel_type}]->(w)
    RETURN id(w) AS id
    """
    return tx.run(query, node_id=target_node_id, url=url, title=title, summary=summary, topics=topics, entities=entities, is_serp=is_serp, timestamp=timestamp, dataSource=dataSource).single()["id"]
