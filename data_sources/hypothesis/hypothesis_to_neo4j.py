# hypothesis_to_neo4j.py
import json
import logging
from neo4j import GraphDatabase

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def process_annotation(session, entry):
    # Extract fields with optional handling
    timestamp = entry.get("timestamp")
    url = entry.get("url")
    pageTitle = entry.get("webpageTitle", None)
    annotationID = entry.get("annotationID", None)
    annotationText = entry.get("annotationText", None)
    annotationTextTopics = json.dumps(entry.get("annotationTextTopics", []))
    highlightedText = entry.get("highlightedText")
    highlightedTextTopics = json.dumps(entry.get("highlightedTextTopics", []))
    
    # Add extraction of the missing entity fields
    annotationTextEntities = json.dumps(entry.get("annotationTextEntities", []))
    highlightedTextEntities = json.dumps(entry.get("highlightedTextEntities", []))
    
    tags = json.dumps(entry.get("tags", []))
    userAccount = entry.get("userAccount", None)
    group = entry.get("group", None)
    visibility = entry.get("visibility", "private")
    dataSource = entry.get("dataSource")

    # Update the query to include the new fields for entities
    query = """
    MERGE (w:Webpage {url: $url, title: $pageTitle})
    CREATE (a:Annotation {
        timestamp: $timestamp, 
        dataSource: $dataSource,
        annotationID: $annotationID, 
        text: $annotationText, 
        textTopics: $annotationTextTopics,
        highlightedText: $highlightedText,
        highlightedTextTopics: $highlightedTextTopics,
        annotationTextEntities: $annotationTextEntities,
        highlightedTextEntities: $highlightedTextEntities,
        tags: $tags,
        userAccount: $userAccount, 
        group: $group,
        visibility: $visibility,
        isInput: false
    })
    MERGE (w)-[:HAS_ANNOTATION]->(a)
    RETURN id(a) AS annotation_id
    """
    parameters = {
        "timestamp": timestamp, "url": url, "pageTitle": pageTitle,
        "dataSource": dataSource, "annotationID": annotationID, "annotationText": annotationText,
        "annotationTextTopics": annotationTextTopics, "highlightedText": highlightedText,
        "highlightedTextTopics": highlightedTextTopics, "annotationTextEntities": annotationTextEntities,
        "highlightedTextEntities": highlightedTextEntities, "tags": tags, "userAccount": userAccount,
        "group": group, "visibility": visibility
    }
    result = session.run(query, parameters)
    annotation_id = result.single()["annotation_id"]

    logging.info(f"Processed annotation for URL: {url} at timestamp: {timestamp}")
    return annotation_id
