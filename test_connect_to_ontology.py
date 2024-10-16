import logging
from neo4j import GraphDatabase
from connect_to_ontology import (
    initialize_cache_db,
    get_batch_of_node_ids,
    get_user_data_nodes,
    process_all_uris,
    update_node_status_in_db,
    get_from_cache,
    save_to_cache,
    batch_query_wikidata,
    create_or_link_wikidata_ontology_node,
    extract_uris_from_node_data,  # Added import
    get_score_from_node_data,     # Added import
    STATE_DB_PATH,
    CACHE_DB_PATH
)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize the driver instance globally
uri = "bolt://localhost:7687"
username = "neo4j"
password = "Coyote1??"  # Replace with your actual password
driver = GraphDatabase.driver(uri, auth=(username, password))

def test_initialize_cache_db():
    """Test the cache database initialization."""
    initialize_cache_db()
    print("Cache database initialized.")

def test_get_batch():
    """Test fetching a batch of node IDs."""
    node_ids = get_batch_of_node_ids(db_path=STATE_DB_PATH)
    print(f"Node IDs fetched: {node_ids}")

def test_get_user_data_nodes():
    """Test fetching user data nodes from Neo4j."""
    with driver.session() as session:
        node_ids = get_batch_of_node_ids(db_path=STATE_DB_PATH)
        if node_ids:
            nodes = get_user_data_nodes(session, node_ids)
            print(f"User data nodes fetched: {nodes}")
        else:
            print("No node IDs were fetched.")

def test_batch_query_wikidata():
    """Test batch querying Wikidata."""
    uris = [
        "http://www.wikidata.org/entity/Q1",  # Universe
        "http://www.wikidata.org/entity/Q2",  # Earth
        "http://www.wikidata.org/entity/Q3",  # God
    ]
    results = batch_query_wikidata(uris)
    print(f"Batch query results: {results}")

def test_cache_functions():
    """Test the cache get and save functions."""
    test_uri = "http://www.wikidata.org/entity/Q1"
    # Save to cache
    test_data = [{"parent": "http://www.wikidata.org/entity/Q2", "parentLabel": "Earth", "relationship": "instance of"}]
    save_to_cache(test_uri, test_data)
    # Get from cache
    retrieved_data = get_from_cache(test_uri)
    print(f"Retrieved data from cache for URI {test_uri}: {retrieved_data}")

def test_process_all_uris():
    with driver.session() as session:
        while True:  # Loop to process multiple batches
            node_ids = get_batch_of_node_ids(db_path=STATE_DB_PATH)
            if node_ids:
                nodes = get_user_data_nodes(session, node_ids)

                # Print node IDs and their data for debugging
                for nid, node_data in nodes.items():
                    print(f"Node ID: {nid}, Node Data: {node_data}")

                # Check if there are any Annotation nodes in the batch
                annotation_nodes = [
                    nid for nid, node_data in nodes.items()
                    if (node_data.get('annotationTextEntities') and node_data['annotationTextEntities'] not in [None, '[]']) or
                       (node_data.get('highlightedTextEntities') and node_data['highlightedTextEntities'] not in [None, '[]'])
                ]
                if annotation_nodes:
                    print(f"Annotation nodes found: {annotation_nodes}")

                # Process the nodes and update their statuses
                process_all_uris(session, nodes, update_node_status_in_db)
            else:
                print("No more node IDs to fetch, processing complete.")
                break  # Exit the loop when there are no more pending nodes


def test_create_or_link_wikidata_ontology_node():
    """Test creating or linking Wikidata ontology nodes."""
    with driver.session() as session:
        # Assume `node_ids` have been fetched and `nodes_data` has been processed
        node_ids = get_batch_of_node_ids(db_path=STATE_DB_PATH)
        if node_ids:
            nodes_data = get_user_data_nodes(session, node_ids)
            # Extract URIs for testing
            for node_id, data in nodes_data.items():
                uris = extract_uris_from_node_data(data)
                timestamp = data.get('timestamp', '')
                score = get_score_from_node_data(data)
                for uri in uris:
                    # Get parent data from cache or batch query
                    parent_data_list = get_from_cache(uri)
                    if not parent_data_list:
                        parent_data_list = batch_query_wikidata([uri]).get(uri, [])
                    # Test the function
                    create_or_link_wikidata_ontology_node(
                        session, node_id, uri, parent_data_list, timestamp, score, 1, [uri]
                    )
            # Check if relationships exist, specifically for Annotation nodes
            result = session.run("""
                MATCH (n)-[r]->(m:WikiDataOntology)
                WHERE id(n) IN $node_ids
                RETURN COUNT(r) AS relationshipsCount
            """, node_ids=node_ids)
            record = result.single()
            relationships_count = record["relationshipsCount"]
            print(f"Number of relationships created: {relationships_count}")
        else:
            print("No node IDs were fetched.")

if __name__ == "__main__":
    try:
        # Initialize cache database
        test_initialize_cache_db()
        
        # Test the connection
        with driver.session() as session:
            result = session.run("MATCH (n) RETURN count(n) AS count")
            record = result.single()
            node_count = record["count"]
            print(f"Connected to Neo4j! Node count: {node_count}")

        # Run tests
        test_get_batch()
        test_get_user_data_nodes()
        test_cache_functions()
        test_batch_query_wikidata()
        test_process_all_uris()
        test_create_or_link_wikidata_ontology_node()

    except Exception as e:
        print(f"Failed during execution: {e}")

    finally:
        driver.close()  # Ensure the driver is closed at the end
