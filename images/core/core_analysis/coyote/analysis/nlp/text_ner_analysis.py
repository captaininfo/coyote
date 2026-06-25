"""
text_ner_analysis.py

Module for extracting named entities from text using spaCy NER and
mapping them to WikiData entities.

Unit 3 M5/M6 (2026-06-11): WikiData imports retargeted to
coyote.analysis.wikidata_lookup; deleted the dead TF-IDF chain
(calculate_tfidf_on_phrases / map_tfidf_to_wikidata / combine_nlp_results /
get_ner_from_text — zero external callers), the shadowed ner.py import
(which loaded a third unused spaCy instance), and the stopwords block
(only the dead chain consumed it). Phase 5 removed the module-level
spaCy load: extract_entities now takes the caller-owned instance.
"""

import logging
from typing import List, Dict, Tuple, Any, Optional

from coyote.analysis.wikidata_lookup import (
    _INVISIBLE_CHARS,
    query_wikidata,
)

logger = logging.getLogger(__name__)


def extract_entities(text: str, nlp) -> List[Tuple[str, str]]:
    """
    Extract named entities from the given text using spaCy.

    Args:
        text (str): The text to analyze.
        nlp: The caller-owned spaCy Language instance (Unit 3c constructor
            injection — the NLP state manager loads one full-pipeline
            instance and shares it between NER and KeyBERT noun_chunks).

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


def map_ner_to_wikidata(
    entities: List[str], context_embedding: Optional[List[float]] = None
) -> Dict[str, Dict[str, Any]]:
    """
    Map a list of entity strings to WikiData URIs.

    When context_embedding is provided (webpage path only — the page's pooled
    full-doc embedding), Unit 8 re-ranks each entity's candidate list by
    description<->context cosine and may DECLINE a mapping; when None (every
    other event path, or a webpage whose own embedding failed) the prominence
    top-1 is used unchanged.

    Returns:
        Dict[str, Dict[str, Any]]: Mapped entities with replacements, URIs, and labels (labels from WikiData).
    """
    # Function-local import: avoid enlarging this module's import surface / any cycle.
    from coyote.analysis.nlp.wikidata_disambiguation import select_best_candidate
    try:
        mapped_entities = {}
        for entity in entities:
            if not entity or not entity.strip(_INVISIBLE_CHARS):
                continue
            wikidata_result = query_wikidata(entity)
            if not wikidata_result:
                continue
            if context_embedding is not None:
                selected = select_best_candidate(
                    context_embedding, wikidata_result, term=entity
                )
                if selected is None:
                    continue  # Unit 8 declined below threshold -> no mapping
                wikidata_label, wikidata_uri = selected
            else:
                wikidata_label, wikidata_uri, _ = wikidata_result[0]  # prominence top-1
            mapped_entities[entity] = {
                'replacement': entity.replace(" ", "_"),
                'uri': wikidata_uri,
                'label': wikidata_label  # from WikiData
            }
        logger.debug(f"Mapped Entities to WikiData: {mapped_entities}")
        return mapped_entities
    except Exception as e:
        logger.error(f"Error in map_ner_to_wikidata: {e}")
        return {}
