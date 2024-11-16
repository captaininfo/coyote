# coyote_main.py

import json
import logging
import uuid  # Import uuid for generating UUIDs
from pathlib import Path
from typing import Dict, Any, Optional, List

# Import application modules
from coyote.analysis.scrape_webpage import scrape_webpage
from coyote.analysis.summarize_text import summarize_text
from coyote.analysis.nlp.text_ner_analysis import get_ner_from_text
from coyote.analysis.nlp.text_bertopic_analysis import get_topic_from_text
from coyote.analysis.relevance_calculator import calculate_relevance

# Define base directories
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
LOGS_DIR = DATA_DIR / 'logs'
ANALYSIS_FILE = DATA_DIR / 'analysis_result.json'

# Ensure the data and logs directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Configure logging
LOG_FILE = LOGS_DIR / 'coyote.log'
logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Get the logger for this module
logger = logging.getLogger(__name__)


def append_to_json_file(file_path: Path, data: Dict[str, Any]) -> None:
    """
    Append data to a JSON file. If the file does not exist, create it.

    Args:
        file_path (Path): The path to the JSON file.
        data (Dict[str, Any]): The data to append.

    Raises:
        Exception: If an error occurs during file operations.
    """
    try:
        if file_path.exists():
            with file_path.open("r+", encoding='utf-8') as file:
                file_data = json.load(file)
                file_data.append(data)
                file.seek(0)
                json.dump(file_data, file, indent=4)
        else:
            with file_path.open("w", encoding='utf-8') as file:
                json.dump([data], file, indent=4)
    except Exception as e:
        logger.error(f"Failed to append data to {file_path}: {e}", exc_info=True)
        raise


def process_hypothesis_annotations(annotations: List[Dict[str, Any]]) -> None:
    """
    Process Hypothesis annotations and append analysis results to the JSON file.

    Args:
        annotations (List[Dict[str, Any]]): A list of annotations from Hypothesis.
    """
    for annotation in annotations:
        try:
            event_id = str(uuid.uuid4())  # Generate a UUID for each annotation
            text = annotation.get('text', '')
            topics_data = get_topic_from_text(text) if text else {"topics_with_weights": {}, "mapped_topics": []}
            ner_data = get_ner_from_text(text) if text else {"topics_with_weights": {}, "mapped_topics": []}

            highlighted_text = "".join([
                sel.get('exact', '')
                for sel in annotation['target'][0].get('selector', [])
                if sel.get('type') == 'TextQuoteSelector'
            ])

            highlighted_topics_data = get_topic_from_text(highlighted_text) if highlighted_text else {"topics_with_weights": {}, "mapped_topics": []}
            highlighted_ner_data = get_ner_from_text(highlighted_text) if highlighted_text else {"topics_with_weights": {}, "mapped_topics": []}

            annotation_data = {
                "event_id": event_id,  # Include event_id in the data
                "timestamp": annotation['created'],
                "event": "User annotated webpage",
                "dataSource": "Hypothesis",
                "url": annotation['uri'],
                "webpageTitle": annotation['document']['title'][0] if annotation['document'].get('title') else '',
                "annotationID": annotation['id'],
                "annotationText": text,
                "annotationTextTopics": topics_data["mapped_topics"],
                "annotationTextEntities": ner_data["mapped_topics"],
                "highlightedText": highlighted_text,
                "highlightedTextTopics": highlighted_topics_data["mapped_topics"],
                "highlightedTextEntities": highlighted_ner_data["mapped_topics"],
                "tags": annotation.get('tags', []),
                "userAccount": annotation['user'],
                "group": annotation['group'],
                "visibility": "public" if "group:__world__" in annotation['permissions']['read'] else "private"
            }
            append_to_json_file(ANALYSIS_FILE, annotation_data)
            # Record the event_id using coyote_state_manager.py
            record_event_id(event_id)
            logger.debug(f"Processed annotation ID: {annotation['id']} with event_id: {event_id}")
        except Exception as e:
            logger.error(f"Error processing annotation ID {annotation.get('id')}: {e}", exc_info=True)

    # Trigger json_to_neo4j.py after processing all annotations
    trigger_json_to_neo4j()


def is_google_serp(url: str) -> bool:
    """
    Check if a URL is a Google search results page.

    Args:
        url (str): The URL to check.

    Returns:
        bool: True if the URL is a Google SERP, False otherwise.
    """
    return "google.com/search" in url


def process_data_from_server(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process data received from the server and perform analysis.

    Args:
        data (Dict[str, Any]): The data received from the server.

    Returns:
        Dict[str, Any]: A status message indicating success or error.
    """
    if data.get('event') == "User annotated webpage":
        process_hypothesis_annotations(data['annotations'])
    else:
        try:
            event_id = str(uuid.uuid4())  # Generate a UUID for the event
            results = {
                "event_id": event_id,  # Include event_id in the data
                "timestamp": data['timestamp'],
                "event": data.get('event'),
                "dataSource": data.get('dataSource', 'Coyote Browser Extension')
            }

            if data['event'] == 'User starts or modifies a search':
                purpose = data.get('purpose', '')
                search_terms = data.get('searchTerms', '')

                purpose_topics_data = get_topic_from_text(purpose)
                purpose_ner_data = get_ner_from_text(purpose)
                search_terms_topics_data = get_topic_from_text(search_terms)
                search_terms_ner_data = get_ner_from_text(search_terms)

                relevance_score = calculate_relevance(
                    purpose_topics_data["topics_with_weights"],
                    search_terms_topics_data["topics_with_weights"]
                )

                results.update({
                    "purpose": purpose,
                    "purposeTopics": purpose_topics_data["mapped_topics"],
                    "purposeEntities": purpose_ner_data["mapped_topics"],
                    "searchTerms": search_terms,
                    "searchTermsTopics": search_terms_topics_data["mapped_topics"],
                    "searchTermsEntities": search_terms_ner_data["mapped_topics"],
                    "searchTerms_relevanceScores": relevance_score
                })

            elif data['event'] == 'Webpage loads':
                url = data.get('url', '')
                if is_google_serp(url):
                    # Skip NLP analysis for Google SERP pages
                    results.update({
                        "url": url,
                        "webpageTitle": data.get('title', ''),
                    })
                else:
                    webpage_text = scrape_webpage(url)
                    summary = summarize_text(webpage_text) or ""
                    topics_data = get_topic_from_text(webpage_text)
                    summary_topics_data = get_topic_from_text(summary)
                    ner_data = get_ner_from_text(webpage_text)

                    relevance_score = calculate_relevance(
                        topics_data["topics_with_weights"],
                        summary_topics_data["topics_with_weights"]
                    ) if summary_topics_data else 0.0

                    results.update({
                        "url": url,
                        "webpageTitle": data.get('title', ''),
                        "webpageSummary": summary,
                        "webpageTopics": [
                            {"topic": k, "uri": v['uri'], "score": v['score']}
                            for k, v in topics_data["topics_with_weights"].items()
                        ],
                        "webpageNamedEntities": [
                            {"entity": k, "uri": v['uri'], "score": v['score']}
                            for k, v in ner_data["topics_with_weights"].items()
                        ],
                        "webpage_relevanceScores": relevance_score
                    })

            elif data['event'] == 'User clicks hyperlink':
                hyperlink_text = data.get('linkText', '')
                hyperlink_topics_data = get_topic_from_text(hyperlink_text)
                hyperlink_ner_data = get_ner_from_text(hyperlink_text)

                results.update({
                    "sourceURL": data.get('sourceURL', ''),
                    "destinationURL": data.get('destinationURL', ''),
                    "linkText": hyperlink_text,
                    "hyperlinkTopics": [
                        {"topic": k, "uri": v['uri'], "score": v['score']}
                        for k, v in hyperlink_topics_data["topics_with_weights"].items()
                    ],
                    "hyperlinkEntities": [
                        {"entity": k, "uri": v['uri'], "score": v['score']}
                        for k, v in hyperlink_ner_data["topics_with_weights"].items()
                    ]
                })

            append_to_json_file(ANALYSIS_FILE, results)
            # Record the event_id using coyote_state_manager.py
            record_event_id(event_id)
            # Trigger json_to_neo4j.py
            trigger_json_to_neo4j()
            logger.debug(f"Processed event: {data.get('event')} with event_id: {event_id}")
            return {"status": "success", "message": "Data processed and stored."}

        except Exception as e:
            logger.error(f"Error processing data: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}


def record_event_id(event_id: str) -> None:
    """
    Record the event_id in the state manager by adding it to the event queue.

    Args:
        event_id (str): The unique identifier of the event.
    """
    from coyote.utils.coyote_state_manager import CoyoteStateManager
    state_manager = CoyoteStateManager()
    state_manager.add_event_to_queue(event_id)  
    state_manager.close()


def trigger_json_to_neo4j() -> None:
    """
    Trigger the json_to_neo4j.py script to process new events.
    """
    logger.info("'coyote_main' triggering json_to_neo4j.")
    from coyote.neo4j_integration.json_to_neo4j import main as json_to_neo4j_main
    try:
        json_to_neo4j_main()
        logger.info("Successfully triggered json_to_neo4j.")
    except Exception as e:
        logger.error(f"Error triggering json_to_neo4j: {e}", exc_info=True)


def main() -> None:
    """
    Main entry point for the Coyote application.
    """
    logger.info("Coyote application is running...")
    # Add any startup code or initializations here
    # For example, start a server or process initial data


