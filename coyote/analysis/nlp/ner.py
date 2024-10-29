"""
ner.py

Module for extracting named entities from text using spaCy.
"""

import logging
from typing import List, Tuple, Optional

import spacy

logger = logging.getLogger(__name__)

# Load spaCy model at module level
try:
    nlp = spacy.load("en_core_web_sm")
except Exception as e:
    logger.error(f"Failed to load spaCy model: {e}")
    nlp = None  # Handle initialization failure


def extract_entities(text: str) -> List[Tuple[str, str]]:
    """
    Extract named entities from the given text using spaCy.

    Args:
        text (str): The text to analyze.

    Returns:
        List[Tuple[str, str]]: A list of tuples containing entities and their labels.
        Returns an empty list if no entities are found or an error occurs.
    """
    if nlp is None:
        logger.error("spaCy model is not initialized.")
        return []

    try:
        if not text.strip():
            logger.warning("Empty or whitespace-only text provided to extract_entities.")
            return []

        doc = nlp(text)
        entities = [(ent.text, ent.label_) for ent in doc.ents]
        logger.debug(f"Extracted Entities: {entities}")
        return entities
    except Exception as e:
        logger.error(f"Error in extract_entities: {e}", exc_info=True)
        return []
