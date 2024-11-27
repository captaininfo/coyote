# coyote_main.py

"""
coyote_main.py

Main application script for the Coyote application.
"""

import sqlite3
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
from coyote.utils.config_manager import (
    get_event_data_db_connection,
    get_state_db_connection  # If needed
)


# Define base directories
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
LOGS_DIR = DATA_DIR / 'logs'
ANALYSIS_FILE = DATA_DIR / 'analysis_result.json'

# Ensure the data and logs directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)


# Get the logger for this module
logger = logging.getLogger(__name__)


def insert_event(event_data: Dict[str, Any]) -> None:
    """
    Inserts an event into the Events table.

    Args:
        event_data (Dict[str, Any]): A dictionary containing event data.
    """
    try:
        conn = get_event_data_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO Events (event_id, timestamp, event_type, data_source, processed)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            event_data['event_id'],
            event_data['timestamp'],
            event_data['event_type'],
            event_data.get('data_source', 'Coyote Browser Extension'),
            0  # processed flag set to 0 by default
        ))
        conn.commit()
        logger.debug(f"Inserted event {event_data['event_id']} into Events table.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error in insert_event: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error in insert_event: {e}", exc_info=True)


def insert_search_event(search_event_data: Dict[str, Any]) -> None:
    """
    Inserts a search event into the SearchEvents table.

    Args:
        search_event_data (Dict[str, Any]): A dictionary containing search event data.
    """
    try:
        conn = get_event_data_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO SearchEvents (event_id, purpose, search_terms, search_terms_relevance_score)
            VALUES (?, ?, ?, ?)
        ''', (
            search_event_data['event_id'],
            search_event_data.get('purpose', ''),
            search_event_data.get('search_terms', ''),
            search_event_data.get('search_terms_relevance_score', 0.0)
        ))
        conn.commit()
        logger.debug(f"Inserted search event {search_event_data['event_id']} into SearchEvents table.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error in insert_search_event: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error in insert_search_event: {e}", exc_info=True)


def insert_webpage_loads_event(webpage_loads_event_data: Dict[str, Any]) -> None:
    """
    Inserts a webpage load event into the WebpageLoads table.

    Args:
        webpage_loads_event_data (Dict[str, Any]): A dictionary containing webpage load event data.
    """
    try:
        conn = get_event_data_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO WebpageLoads (event_id, url, webpage_title, webpage_summary, webpage_relevance_score)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            webpage_loads_event_data['event_id'],
            webpage_loads_event_data.get('url', ''),
            webpage_loads_event_data.get('webpage_title', ''),
            webpage_loads_event_data.get('webpage_summary', ''),
            webpage_loads_event_data.get('webpage_relevance_score', 0.0)
        ))
        conn.commit()
        logger.debug(
            f"Inserted webpage load event {webpage_loads_event_data['event_id']} into WebpageLoads table."
        )
    except sqlite3.Error as e:
        logger.error(f"SQLite error in insert_webpage_loads_event: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error in insert_webpage_loads_event: {e}", exc_info=True)


def insert_hyperlink_click_event(hyperlink_click_event_data: Dict[str, Any]) -> None:
    """
    Inserts a hyperlink click event into the HyperlinkClicks table.

    Args:
        hyperlink_click_event_data (Dict[str, Any]): A dictionary containing hyperlink click event data.
    """
    try:
        conn = get_event_data_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO HyperlinkClicks (event_id, source_url, destination_url, link_text)
            VALUES (?, ?, ?, ?)
        ''', (
            hyperlink_click_event_data['event_id'],
            hyperlink_click_event_data.get('source_url', ''),
            hyperlink_click_event_data.get('destination_url', ''),
            hyperlink_click_event_data.get('link_text', '')
        ))
        conn.commit()
        logger.debug(
            f"Inserted hyperlink click event {hyperlink_click_event_data['event_id']} into HyperlinkClicks table."
        )
    except sqlite3.Error as e:
        logger.error(f"SQLite error in insert_hyperlink_click_event: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error in insert_hyperlink_click_event: {e}", exc_info=True)


def insert_annotation_event(annotation_event_data: Dict[str, Any]) -> None:
    """
    Inserts an annotation event into the Annotations table.

    Args:
        annotation_event_data (Dict[str, Any]): A dictionary containing annotation event data.
    """
    try:
        conn = get_event_data_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO Annotations (
                event_id,
                url,
                webpage_title,
                annotation_id,
                annotation_text,
                highlighted_text,
                user_account,
                group_id,
                visibility
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            annotation_event_data['event_id'],
            annotation_event_data.get('url', ''),
            annotation_event_data.get('webpage_title', ''),
            annotation_event_data.get('annotation_id', ''),
            annotation_event_data.get('annotation_text', ''),
            annotation_event_data.get('highlighted_text', ''),
            annotation_event_data.get('user_account', ''),
            annotation_event_data.get('group_id', ''),
            annotation_event_data.get('visibility', '')
        ))
        conn.commit()
        logger.debug(
            f"Inserted annotation event {annotation_event_data['event_id']} into Annotations table."
        )
    except sqlite3.Error as e:
        logger.error(f"SQLite error in insert_annotation_event: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error in insert_annotation_event: {e}", exc_info=True)


def insert_entity(entity_data: Dict[str, Any]) -> None:
    """
    Inserts an entity into the Entities table.

    Args:
        entity_data (Dict[str, Any]): A dictionary containing entity data.
    """
    try:
        conn = get_event_data_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO Entities (event_id, entity_context, entity, uri, score)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            entity_data['event_id'],
            entity_data.get('entity_context', ''),
            entity_data.get('entity', ''),
            entity_data.get('uri', ''),
            entity_data.get('score', 0.0)
        ))
        conn.commit()
        logger.debug(
            f"Inserted entity '{entity_data.get('entity', '')}' for event {entity_data['event_id']} into Entities table."
        )
    except sqlite3.Error as e:
        logger.error(f"SQLite error in insert_entity: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error in insert_entity: {e}", exc_info=True)

def insert_topic(topic_data: Dict[str, Any]) -> None:
    """
    Inserts a topic into the Topics table.

    Args:
        topic_data (Dict[str, Any]): A dictionary containing topic data.
    """
    try:
        conn = get_event_data_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO Topics (event_id, topic_context, topic, uri, score)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            topic_data['event_id'],
            topic_data.get('topic_context', ''),
            topic_data.get('topic', ''),
            topic_data.get('uri', ''),
            topic_data.get('score', 0.0)
        ))
        conn.commit()
        logger.debug(
            f"Inserted topic '{topic_data.get('topic', '')}' for event {topic_data['event_id']} into Topics table."
        )
    except sqlite3.Error as e:
        logger.error(f"SQLite error in insert_topic: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error in insert_topic: {e}", exc_info=True)


def insert_annotation_tag(annotation_tag_data: Dict[str, Any]) -> None:
    """
    Inserts an annotation tag into the AnnotationTags table.

    Args:
        annotation_tag_data (Dict[str, Any]): A dictionary containing annotation tag data.
    """
    try:
        conn = get_event_data_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO AnnotationTags (event_id, annotation_id, tag)
            VALUES (?, ?, ?)
        ''', (
            annotation_tag_data['event_id'],
            annotation_tag_data.get('annotation_id', ''),
            annotation_tag_data.get('tag', '')
        ))
        conn.commit()
        logger.debug(
            f"Inserted annotation tag '{annotation_tag_data.get('tag', '')}' for event {annotation_tag_data['event_id']} into AnnotationTags table."
        )
    except sqlite3.Error as e:
        logger.error(f"SQLite error in insert_annotation_tag: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error in insert_annotation_tag: {e}", exc_info=True)


def is_google_serp(url: str) -> bool:
    """
    Check if a URL is a Google search results page.

    Args:
        url (str): The URL to check.

    Returns:
        bool: True if the URL is a Google SERP, False otherwise.
    """
    return "google.com/search" in url


def insert_topics_and_entities(event_id: str, context: str, topics_data: Dict[str, Any], entities_data: Dict[str, Any]) -> None:
    """
    Inserts topics and entities into the database.

    Args:
        event_id (str): The event ID.
        context (str): The context of the topics/entities (e.g., 'purpose', 'search_terms').
        topics_data (Dict[str, Any]): The topics data.
        entities_data (Dict[str, Any]): The entities data.
    """
    # Insert topics
    for topic, details in topics_data.get('topics_with_weights', {}).items():
        topic_data = {
            'event_id': event_id,
            'topic_context': context,
            'topic': topic,
            'uri': details.get('uri', ''),
            'score': details.get('score', 0.0)
        }
        insert_topic(topic_data)

    # Insert entities
    for entity, details in entities_data.get('topics_with_weights', {}).items():
        entity_data = {
            'event_id': event_id,
            'entity_context': context,
            'entity': entity,
            'uri': details.get('uri', ''),
            'score': details.get('score', 0.0)
        }
        insert_entity(entity_data)



def process_data_from_server(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process data received from the server, perform analysis, and store results in the database.

    Args:
        data (Dict[str, Any]): The data received from the server.

    Returns:
        Dict[str, Any]: A status message indicating success or error.
    """
    try:
        event_id = str(uuid.uuid4())  # Generate a UUID for the event
        timestamp = data['timestamp']
        event_type = data.get('event')
        data_source = data.get('dataSource', 'Coyote Browser Extension')

        # Insert the event into the Events table
        event_data = {
            'event_id': event_id,
            'timestamp': timestamp,
            'event_type': event_type,
            'data_source': data_source
        }
        insert_event(event_data)

        if event_type == 'User starts or modifies a search':
            purpose = data.get('purpose', '')
            search_terms = data.get('searchTerms', '')

            # Perform NLP analysis
            purpose_topics_data = get_topic_from_text(purpose)
            purpose_ner_data = get_ner_from_text(purpose)
            search_terms_topics_data = get_topic_from_text(search_terms)
            search_terms_ner_data = get_ner_from_text(search_terms)

            # Calculate relevance score
            relevance_score = calculate_relevance(
                purpose_topics_data["topics_with_weights"],
                search_terms_topics_data["topics_with_weights"]
            )

            # Insert search event data
            search_event_data = {
                'event_id': event_id,
                'purpose': purpose,
                'search_terms': search_terms,
                'search_terms_relevance_score': relevance_score
            }
            insert_search_event(search_event_data)

            # Insert topics and entities
            insert_topics_and_entities(
                event_id, 'purpose', purpose_topics_data, purpose_ner_data
            )
            insert_topics_and_entities(
                event_id, 'search_terms', search_terms_topics_data, search_terms_ner_data
            )

        elif event_type == 'Webpage loads':
            url = data.get('url', '')
            webpage_title = data.get('title', '')

            if is_google_serp(url):
                # Skip NLP analysis for Google SERP pages
                webpage_summary = ''
                relevance_score = 0.0
                topics_data = {"topics_with_weights": {}}
                ner_data = {"topics_with_weights": {}}
            else:
                # Scrape webpage and perform analysis
                webpage_text = scrape_webpage(url)
                webpage_summary = summarize_text(webpage_text) or ""
                topics_data = get_topic_from_text(webpage_text)
                summary_topics_data = get_topic_from_text(webpage_summary)
                ner_data = get_ner_from_text(webpage_text)

                # Calculate relevance score
                relevance_score = calculate_relevance(
                    topics_data["topics_with_weights"],
                    summary_topics_data["topics_with_weights"]
                ) if summary_topics_data else 0.0

            # Insert webpage load event data
            webpage_loads_event_data = {
                'event_id': event_id,
                'url': url,
                'webpage_title': webpage_title,
                'webpage_summary': webpage_summary,
                'webpage_relevance_score': relevance_score
            }
            insert_webpage_loads_event(webpage_loads_event_data)

            # Insert topics and entities
            insert_topics_and_entities(
                event_id, 'webpage', topics_data, ner_data
            )

        elif event_type == 'User clicks hyperlink':
            source_url = data.get('sourceURL', '')
            destination_url = data.get('destinationURL', '')
            link_text = data.get('linkText', '')

            # Perform NLP analysis
            hyperlink_topics_data = get_topic_from_text(link_text)
            hyperlink_ner_data = get_ner_from_text(link_text)

            # Insert hyperlink click event data
            hyperlink_click_event_data = {
                'event_id': event_id,
                'source_url': source_url,
                'destination_url': destination_url,
                'link_text': link_text
            }
            insert_hyperlink_click_event(hyperlink_click_event_data)

            # Insert topics and entities
            insert_topics_and_entities(
                event_id, 'hyperlink', hyperlink_topics_data, hyperlink_ner_data
            )

        elif event_type == 'User annotated webpage':
            # Process each annotation
            for annotation in data.get('annotations', []):
                annotation_event_id = str(uuid.uuid4())
                annotation_event_data = {
                    'event_id': annotation_event_id,
                    'timestamp': annotation['created'],
                    'event_type': 'User annotated webpage',
                    'data_source': 'Hypothesis'
                }
                insert_event(annotation_event_data)

                # Extract data from annotation
                annotation_id = annotation.get('id', '')
                url = annotation.get('uri', '')
                webpage_title = annotation.get('document', {}).get('title', [''])[0]
                annotation_text = annotation.get('text', '')
                highlighted_text = "".join([
                    sel.get('exact', '')
                    for sel in annotation['target'][0].get('selector', [])
                    if sel.get('type') == 'TextQuoteSelector'
                ])
                user_account = annotation.get('user', '')
                group_id = annotation.get('group', '')
                visibility = 'public' if 'group:__world__' in annotation.get('permissions', {}).get('read', []) else 'private'

                # Insert annotation event data
                annotation_event_data = {
                    'event_id': annotation_event_id,
                    'url': url,
                    'webpage_title': webpage_title,
                    'annotation_id': annotation_id,
                    'annotation_text': annotation_text,
                    'highlighted_text': highlighted_text,
                    'user_account': user_account,
                    'group_id': group_id,
                    'visibility': visibility
                }
                insert_annotation_event(annotation_event_data)

                # Insert annotation tags
                for tag in annotation.get('tags', []):
                    annotation_tag_data = {
                        'event_id': annotation_event_id,
                        'annotation_id': annotation_id,
                        'tag': tag
                    }
                    insert_annotation_tag(annotation_tag_data)

                # Perform NLP analysis on annotation text and highlighted text
                annotation_text_topics_data = get_topic_from_text(annotation_text)
                annotation_text_ner_data = get_ner_from_text(annotation_text)
                highlighted_text_topics_data = get_topic_from_text(highlighted_text)
                highlighted_text_ner_data = get_ner_from_text(highlighted_text)

                # Insert topics and entities
                insert_topics_and_entities(
                    annotation_event_id, 'annotation_text', annotation_text_topics_data, annotation_text_ner_data
                )
                insert_topics_and_entities(
                    annotation_event_id, 'highlighted_text', highlighted_text_topics_data, highlighted_text_ner_data
                )

        else:
            logger.warning(f"Unhandled event type: {event_type}")

        # Return success message
        return {"status": "success", "message": "Data processed and stored in database."}

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
    from coyote.neo4j_integration.events_to_neo4j import main as events_to_neo4j_main
    try:
        events_to_neo4j_main()
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


