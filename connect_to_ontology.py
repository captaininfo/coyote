import sqlite3
import logging
import json
import time
import datetime
from neo4j import GraphDatabase
from SPARQLWrapper import SPARQLWrapper, JSON
from config_manager import get_setting  # Import get_setting from config_manager.py

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global configuration
MAX_RECURSION_DEPTH = 5  # Limit recursion depth
TOP_LEVEL_URI = "http://www.wikidata.org/entity/Q35120"  # Top-level 'entity' node
STATE_DB_PATH = 'coyote_state.db'
CACHE_DB_PATH = 'wikidata_cache.db'
CACHE_EXPIRATION_DAYS = 7  # Cache expiration time in days

def initialize_cache_db():
    """Initializes the SQLite database used for caching Wikidata queries."""
    conn = sqlite3.connect(CACHE_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS wikidata_cache (
        uri TEXT PRIMARY KEY,
        data TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()
    logger.info("Cache database initialized.")

def get_from_cache(uri):
    """Retrieves data from the cache if available and not expired."""
    conn = sqlite3.connect(CACHE_DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT data, timestamp FROM wikidata_cache WHERE uri=?', (uri,))
    result = cursor.fetchone()
    conn.close()
    if result:
        data, timestamp = result
        # Check for cache expiration
        cache_time = datetime.datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
        if (datetime.datetime.now() - cache_time).days < CACHE_EXPIRATION_DAYS:
            return json.loads(data)
        else:
            # Cache expired
            logger.info(f"Cache expired for URI {uri}")
            return None
    return None

def save_to_cache(uri, data):
    """Saves data to the cache with the current timestamp."""
    conn = sqlite3.connect(CACHE_DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('INSERT OR REPLACE INTO wikidata_cache (uri, data, timestamp) VALUES (?, ?, ?)', (uri, json.dumps(data), timestamp))
    conn.commit()
    conn.close()
    logger.info(f"Data cached for URI {uri}")

def get_batch_of_node_ids(db_path=STATE_DB_PATH, batch_size=10):
    """Fetches a batch of node IDs from the SQLite database that are pending processing."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
        SELECT node_id FROM node_processing_queue
        WHERE status = 'pending'
        ORDER BY created_at ASC
        LIMIT ?
        """, (batch_size,))
        rows = cursor.fetchall()
        node_ids = [row[0] for row in rows]
        
        if node_ids:
            # Update status to 'in_progress'
            cursor.execute("""
            UPDATE node_processing_queue
            SET status = 'in_progress'
            WHERE node_id IN ({})
            """.format(','.join('?' for _ in node_ids)), node_ids)
            conn.commit()
            logger.info(f"Fetched and set to 'in_progress' batch of {len(node_ids)} node IDs.")
        else:
            logger.info("No pending node IDs found.")

        conn.close()
        return node_ids

    except sqlite3.Error as e:
        logger.error(f"SQLite error: {e}")
        return []

def get_user_data_nodes(session, node_ids):
    """Fetches user data nodes from Neo4j using a list of node IDs, focusing on properties containing Wikidata URIs."""
    try:
        query = """
        MATCH (n) WHERE id(n) IN $node_ids
        RETURN id(n) AS node_id, n.entities AS entities, n.topics AS topics, n.textTopics AS textTopics,
        n.annotationTextEntities AS annotationTextEntities, n.highlightedTextEntities AS highlightedTextEntities,
        n.timestamp AS timestamp
        """
        result = session.run(query, node_ids=node_ids)
        
        # Initialize a dictionary to store node IDs and their data
        nodes_data = {}
        
        for record in result:
            node_id = record["node_id"]
            entities = record.get("entities", '[]')
            topics = record.get("topics", '[]')
            text_topics = record.get("textTopics", '[]')
            annotation_text_entities = record.get("annotationTextEntities", '[]')
            highlighted_text_entities = record.get("highlightedTextEntities", '[]')
            timestamp = record.get("timestamp", '')  # Extract timestamp
            
            # Skip nodes with no valid URIs
            if not entities and not topics and not text_topics and not annotation_text_entities and not highlighted_text_entities:
                continue
            
            # Add valid nodes to the dictionary
            nodes_data[node_id] = {
                "entities": entities,
                "topics": topics,
                "textTopics": text_topics,
                "annotationTextEntities": annotation_text_entities,
                "highlightedTextEntities": highlighted_text_entities,
                "timestamp": timestamp  # Include timestamp in nodes_data
            }
        
        logger.info(f"Fetched and processed {len(nodes_data)} relevant nodes from Neo4j.")
        return nodes_data

    except Exception as e:
        logger.error(f"Error querying Neo4j: {e}")
        return {}

def extract_uris_from_node_data(data):
    uris = []
    for key in ['entities', 'topics', 'textTopics', 'annotationTextEntities', 'highlightedTextEntities']:
        if data.get(key):
            try:
                items = json.loads(data[key])
                for item in items:
                    if isinstance(item, list) and len(item) == 2 and item[1].startswith("http"):
                        uris.append(item[1])
                    elif isinstance(item, dict) and 'uri' in item and item['uri']:
                        uris.extend([uri for uri in item['uri'] if uri.startswith("http")])
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error for key '{key}': {e}")
    return [uri for uri in uris if uri]  # Filter out empty URIs

def batch_query_wikidata(uris):
    """Queries Wikidata for a batch of URIs and caches the results."""
    # Remove duplicates
    unique_uris = list(set(uris))
    
    # Check cache and separate cached and uncached URIs
    cached_data = {}
    uncached_uris = []
    for uri in unique_uris:
        data = get_from_cache(uri)
        if data:
            logger.info(f"Cache hit for URI {uri}")
            cached_data[uri] = data
        else:
            uncached_uris.append(uri)
    
    # If there are uncached URIs, query them in batches
    if uncached_uris:
        # Process in batches to avoid exceeding query length limits
        BATCH_SIZE = 50  # Adjust as needed
        for i in range(0, len(uncached_uris), BATCH_SIZE):
            batch_uris = uncached_uris[i:i+BATCH_SIZE]
            uris_str = ' '.join(f"wd:{uri.split('/')[-1]}" for uri in batch_uris)
            query = f"""
            SELECT ?item ?parent ?parentLabel ?relationship
            WHERE {{
                VALUES ?item {{ {uris_str} }}
                OPTIONAL {{
                    ?item wdt:P910 ?parent .
                    BIND("topic's main category" AS ?relationship)
                }}
                OPTIONAL {{
                    ?item wdt:P460 ?parent .
                    BIND("said to be the same as" AS ?relationship)
                }}
                OPTIONAL {{
                    ?item wdt:P279 ?parent .
                    BIND("subclass of" AS ?relationship)
                }}
                OPTIONAL {{
                    ?item wdt:P31 ?parent .
                    BIND("instance of" AS ?relationship)
                }}
                OPTIONAL {{
                    ?item wdt:P361 ?parent .
                    BIND("part of" AS ?relationship)
                }}
                SERVICE wikibase:label {{ bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en". }}
            }}
            """
            sparql = SPARQLWrapper("https://query.wikidata.org/sparql")
            sparql.setQuery(query)
            sparql.setReturnFormat(JSON)
            
            try:
                logger.info(f"Querying Wikidata for batch of {len(batch_uris)} URIs.")
                results = sparql.query().convert()
                # Process results and save to cache
                results_dict = {}
                for result in results["results"]["bindings"]:
                    item_uri = result["item"]["value"]
                    parent_data = {
                        "parent": result.get("parent", {}).get("value", ""),
                        "parentLabel": result.get("parentLabel", {}).get("value", ""),
                        "relationship": result.get("relationship", {}).get("value", "")
                    }
                    # Collect parent data for each item
                    if item_uri in results_dict:
                        results_dict[item_uri].append(parent_data)
                    else:
                        results_dict[item_uri] = [parent_data]
                # Save to cache and update cached_data
                for uri in batch_uris:
                    data_to_cache = results_dict.get(uri, [])
                    save_to_cache(uri, data_to_cache)
                    cached_data[uri] = data_to_cache
                # Add delay between batches to avoid rate limits
                time.sleep(1)  # Adjust as necessary
            except Exception as e:
                logger.error(f"Error querying Wikidata: {e}")
    return cached_data

def process_all_uris(session, nodes_data, update_db_func):
    """
    Iterates over all nodes and their URIs, querying Wikidata in batches,
    and creates relationships between nodes and WikiDataOntology nodes in Neo4j.
    """
    all_uris = []
    node_uri_map = {}  # Map node IDs to their URIs
    for node_id, data in nodes_data.items():
        uris = extract_uris_from_node_data(data)
        if uris:
            node_uri_map[node_id] = uris
            all_uris.extend(uris)
        else:
            logger.info(f"No URIs found for node {node_id}, marking it as processed.")
            update_db_func(node_id, 'processed')
    
    # Batch query Wikidata for all URIs
    uri_parent_data_map = batch_query_wikidata(all_uris)
    
    # Process each node with its URIs
    for node_id, uris in node_uri_map.items():
        data = nodes_data[node_id]
        timestamp = data.get('timestamp', '')
        score = get_score_from_node_data(data)
        for uri in uris:
            parent_data_list = uri_parent_data_map.get(uri, [])
            if parent_data_list:
                create_or_link_wikidata_ontology_node(session, node_id, uri, parent_data_list, timestamp, score, 1, [uri])
            else:
                logger.warning(f"No parent data found for URI {uri}")
        update_db_func(node_id, 'processed')

def get_score_from_node_data(data):
    """Extracts the score from node data if available."""
    score = 0
    try:
        entities = json.loads(data.get('entities', '[]'))
        for entity in entities:
            if isinstance(entity, dict) and 'score' in entity:
                score = entity['score']
                break
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in get_score_from_node_data: {e}")
    return score

def create_or_link_wikidata_ontology_node(session, node_id, uri, parent_data_list, timestamp, score, depth, visited_uris):
    """
    Creates or links a WikiDataOntology node based on the parent data fetched from Wikidata,
    adding timestamp and TF-IDF score to the relationship, and recursively connecting parent nodes.
    """
    try:
        # Check if recursion depth is greater than the allowed limit
        if depth > MAX_RECURSION_DEPTH:
            logger.info(f"Maximum recursion depth reached for node {node_id}. Stopping recursion.")
            return

        # Iterate over each parent dictionary in parent_data_list
        for parent in parent_data_list:
            relationship = parent.get('relationship', '')
            parent_uri = parent.get('parent', '')
            parent_label = parent.get('parentLabel', '')

            if not parent_uri:
                continue

            # Check for cycles by looking at visited_uris
            if parent_uri in visited_uris:
                logger.warning(f"Cycle detected with URI {parent_uri}. Stopping recursion.")
                continue

            visited_uris.append(parent_uri)

            # Determine relationship type
            if node_id:
                # Link from user data node to ontology node using a specific label
                user_to_ontology_rel_type = 'HAS_TOPIC'  # Replace with your chosen label
                create_or_link_node(session, node_id, uri, parent_uri, parent_label, timestamp, score, user_to_ontology_rel_type)
            else:
                # Link between ontology nodes using ontology-specific relationship labels
                if relationship == "topic's main category":
                    rel_type = 'TOPIC_MAIN_CATEGORY'
                    create_or_link_node(session, node_id, uri, parent_uri, parent_label, timestamp, score, rel_type)
                    # Mark the node as a main category
                    set_node_property(session, parent_uri, {'is_main_category': True})
                elif relationship == "said to be the same as":
                    rel_type = 'SAME_AS'
                    create_or_link_node(session, node_id, uri, parent_uri, parent_label, timestamp, score, rel_type)
                elif relationship in ["subclass of", "instance of", "part of"]:
                    rel_type = relationship.upper().replace(' ', '_')
                    create_or_link_node(session, node_id, uri, parent_uri, parent_label, timestamp, score, rel_type)
                else:
                    # Handle other relationships if necessary
                    logger.info(f"Unhandled relationship type: {relationship}")
                    continue

            # Recursion for ontology nodes
            if parent_uri != TOP_LEVEL_URI:
                # Get parent data for the parent URI
                parent_parent_data = get_from_cache(parent_uri)
                if not parent_parent_data:
                    # Query Wikidata for parent URI
                    parent_parent_data = batch_query_wikidata([parent_uri]).get(parent_uri, [])
                # Recursive call
                create_or_link_wikidata_ontology_node(session, None, parent_uri, parent_parent_data, timestamp, score, depth + 1, visited_uris)
            else:
                logger.info(f"Top-level entity node reached for {parent_uri}. Stopping recursion.")

    except Exception as e:
        logger.error(f"Error creating or linking WikiDataOntology node for node ID {node_id}: {e}")

def create_or_link_node(session, node_id, source_uri, target_uri, target_label, timestamp, score, relationship_type):
    """Creates or links nodes and relationships in Neo4j."""
    try:
        # Create or get the target node
        query = """
        MERGE (wdo:WikiDataOntology {uri: $targetUri})
        ON CREATE SET wdo.label = $targetLabel
        """
        session.run(query, targetUri=target_uri, targetLabel=target_label)

        # Create relationship
        if node_id:
            # Link from user data node to ontology node using the specified relationship type
            query = f"""
            MATCH (n) WHERE id(n) = $node_id
            MATCH (wdo:WikiDataOntology {{uri: $targetUri}})
            MERGE (n)-[rel:{relationship_type} {{timestamp: $timestamp, tfidf_score: $score}}]->(wdo)
            """
            session.run(query, node_id=node_id, targetUri=target_uri, timestamp=timestamp, score=score)
            logger.info(f"Linked user node {node_id} to ontology node {target_uri} with relationship {relationship_type}.")
        else:
            # Link between ontology nodes
            query = f"""
            MATCH (source:WikiDataOntology {{uri: $sourceUri}})
            MATCH (target:WikiDataOntology {{uri: $targetUri}})
            MERGE (source)-[rel:{relationship_type}]->(target)
            """
            session.run(query, sourceUri=source_uri, targetUri=target_uri)
            logger.info(f"Linked ontology node {source_uri} to {target_uri} with relationship {relationship_type}.")
    except Exception as e:
        logger.error(f"Error creating or linking nodes: {e}")

def set_node_property(session, uri, properties):
    """Sets properties on a node in Neo4j."""
    try:
        query = """
        MATCH (wdo:WikiDataOntology {uri: $uri})
        SET wdo += $properties
        """
        session.run(query, uri=uri, properties=properties)
        logger.info(f"Set properties {properties} on node {uri}.")
    except Exception as e:
        logger.error(f"Error setting properties on node {uri}: {e}")

def update_node_status_in_db(node_id, status, db_path=STATE_DB_PATH):
    """Update the status of a node in the SQLite database."""
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE node_processing_queue
            SET status = ?
            WHERE node_id = ?
            """, (status, node_id))
            conn.commit()
            logger.info(f"Node ID {node_id} marked as {status}.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error while updating node status: {e}")

def main():
    initialize_cache_db()

    # Get Neo4j credentials from configuration
    uri = get_setting('neo4j_uri')
    username = get_setting('neo4j_username')
    password = get_setting('neo4j_password', decrypt=True)

    # Validate credentials
    if not all([uri, username, password]):
        logger.error("Neo4j credentials not found. Please configure the application.")
        return

    # Create the Neo4j driver
    driver = GraphDatabase.driver(uri, auth=(username, password))

    try:
        with driver.session() as session:
            while True:
                # Fetch a batch of node IDs
                node_ids = get_batch_of_node_ids(db_path=STATE_DB_PATH)
                if node_ids:
                    logger.info(f"Node IDs fetched: {node_ids}")
                    nodes_data = get_user_data_nodes(session, node_ids)
                    if nodes_data:
                        # Process the fetched nodes and update their statuses in the DB
                        process_all_uris(session, nodes_data, update_node_status_in_db)
                    else:
                        logger.info("No user data nodes to process.")
                else:
                    # Break the loop when there are no more pending nodes
                    logger.info("No node IDs fetched from the queue. Processing complete.")
                    break  # Exit the loop
    except Exception as e:
        logger.error(f"An error occurred in the main loop: {e}")
    finally:
        driver.close()
        logger.info("Neo4j driver closed.")

if __name__ == "__main__":
    main()
