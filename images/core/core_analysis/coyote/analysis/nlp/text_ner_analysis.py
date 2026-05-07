"""
text_ner_analysis.py

Module for extracting named entities from text using NER and TF-IDF analysis,
and mapping them to WikiData entities.
"""

import logging
from typing import List, Dict, Tuple, Any

import spacy
from nltk.corpus import stopwords
from sklearn.feature_extraction.text import TfidfVectorizer

from coyote.analysis.nlp.ner import extract_entities
from coyote.analysis.nlp.text_bertopic_analysis import (
    _INVISIBLE_CHARS,
    query_wikidata,
)

logger = logging.getLogger(__name__)

# Load spaCy model at module level
try:
    nlp = spacy.load("en_core_web_sm")
except Exception as e:
    logger.error(f"Failed to load spaCy model: {e}")
    nlp = None  # Handle initialization failure

# Custom list of domain-specific stopwords
custom_stopwords = [
    'page', 'click', 'link', 'comment', 'username', 'password', 'login',
    'subscribe', 'share', 'like', 'read', 'more', 'article', 'posted', 'said'
]

# Combine with standard stopwords
try:
    stop_words_list = list(set(stopwords.words('english')).union(set(custom_stopwords)))
except LookupError:
    import nltk
    nltk.download('stopwords')
    stop_words_list = list(set(stopwords.words('english')).union(set(custom_stopwords)))


def extract_entities(text: str) -> List[Tuple[str, str]]:
    """
    Extract named entities from the given text using spaCy.

    Args:
        text (str): The text to analyze.

    Returns:
        List[Tuple[str, str]]: A list of tuples containing entities and their labels.edr
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


def replace_named_entities_in_text(text: str, entities_mapped: Dict[str, Dict[str, Any]]) -> str:
    """
    Replace entities in text with underscores for multi-word entities.

    Args:
        text (str): The original text.
        entities_mapped (Dict[str, Dict[str, Any]]): Mapped entities with URIs (and possibly labels).

    Returns:
        str: The processed text with entities replaced.
    """
    for entity in entities_mapped.keys():
        replacement = entity.replace(" ", "_")
        text = text.replace(entity, replacement)
    return text



def map_ner_to_wikidata(entities: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Map a list of entity strings to WikiData URIs.

    Returns:
        Dict[str, Dict[str, Any]]: Mapped entities with replacements, URIs, and labels (labels from WikiData).
    """
    try:
        mapped_entities = {}
        for entity in entities:
            if not entity or not entity.strip(_INVISIBLE_CHARS):
                continue
            wikidata_result = query_wikidata(entity)
            if wikidata_result:
                wikidata_label, wikidata_uri = wikidata_result[0]
                mapped_entities[entity] = {
                    'replacement': entity.replace(" ", "_"),
                    'uri': wikidata_uri,
                    'label': wikidata_label  # Using the wikidata_label from the query
                }
        logger.debug(f"Mapped Entities to WikiData: {mapped_entities}")
        return mapped_entities
    except Exception as e:
        logger.error(f"Error in map_ner_to_wikidata: {e}")
        return {}


def calculate_tfidf_on_phrases(
    text: str,
    corpus: List[str],
    threshold: float = 0.15
) -> Dict[str, float]:
    """
    Calculate TF-IDF scores for phrases in the text.

    Args:
        text (str): The text to analyze.
        corpus (List[str]): The corpus of documents for TF-IDF calculation.
        threshold (float): The threshold for including terms based on TF-IDF score.

    Returns:
        Dict[str, float]: Dictionary of terms and their TF-IDF scores.
    """
    try:
        vectorizer = TfidfVectorizer(stop_words=stop_words_list)
        tfidf_matrix = vectorizer.fit_transform(corpus + [text])
        feature_names = vectorizer.get_feature_names_out()
        scores = {
            feature_names[i]: tfidf_matrix[-1, i]
            for i in tfidf_matrix[-1].nonzero()[1]
            if tfidf_matrix[-1, i] > threshold
        }
        logger.debug(f"TF-IDF Scores on Phrases: {scores}")
        return scores
    except Exception as e:
        logger.error(f"Error in calculate_tfidf_on_phrases: {e}")
        return {}


def map_tfidf_to_wikidata(
    tfidf_scores: Dict[str, float]
) -> Dict[str, List[Tuple[str, str]]]:
    """
    Map TF-IDF terms to WikiData URIs.

    Args:
        tfidf_scores (Dict[str, float]): TF-IDF scores for terms.

    Returns:
        Dict[str, List[Tuple[str, str]]]: WikiData results for each term.
    """
    try:
        wikidata_results = {term: query_wikidata(term) for term in tfidf_scores.keys()}
        logger.debug(f"WikiData Results: {wikidata_results}")
        return wikidata_results
    except Exception as e:
        logger.error(f"Error in map_tfidf_to_wikidata: {e}")
        return {}


def combine_nlp_results(
    tfidf_scores: Dict[str, float],
    ner_mapped: Dict[str, Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """
    Combine TF-IDF scores and NER mapped entities into a single result.

    Args:
        tfidf_scores (Dict[str, float]): TF-IDF scores for terms.
        ner_mapped (Dict[str, Dict[str, Any]]): Mapped NER entities.

    Returns:
        Dict[str, Dict[str, Any]]: Combined results with scores and URIs.
    """
    combined_results = {}
    # Map TF-IDF terms to WikiData
    tfidf_wikidata = map_tfidf_to_wikidata(tfidf_scores)

    for term, score in tfidf_scores.items():
        uris = [uri for _, uri in tfidf_wikidata.get(term, [])]
        combined_results[term] = {
            'score': score,
            'uri': uris or [],
            'labels': 'UNKNOWN'
        }

    for entity, data in ner_mapped.items():
        entity_key = data['replacement']
        combined_results[entity_key] = {
            'score': combined_results.get(entity_key, {}).get('score', 0),
            'uri': [data['uri']],
            'labels': data.get('label', 'UNKNOWN')
        }

    logger.debug(f"Combined NER Results: {combined_results}")
    return combined_results


def get_ner_from_text(
    text: str,
    verbose: bool = True
) -> Dict[str, Any]:
    """
    Extract named entities from text and perform TF-IDF analysis.

    Args:
        text (str): The text to analyze.
        verbose (bool): If True, enables verbose logging.

    Returns:
        Dict[str, Any]: A dictionary containing topics with weights and mapped topics.
    """
    try:
        if not text or 'error' in text.lower():
            raise ValueError("Text contains an error message or is empty")

        # Step 1: Extract NER entities
        ner_entities = extract_entities(text)

        # Step 2: Map NER entities to WikiData
        ner_mapped = map_ner_to_wikidata(ner_entities)

        # Step 3: Replace NER entities in text with single-word equivalents
        processed_text = replace_named_entities_in_text(text, ner_mapped)
        if verbose:
            logger.debug(f"Processed Text after replacing entities: {processed_text}")

        # Step 4: Remove stopwords
        processed_text = ' '.join(
            [word for word in processed_text.split() if word.lower() not in stop_words_list]
        )

        # Step 5: Calculate TF-IDF scores
        # Note: Replace with a real corpus in production
        corpus = [
            "Sample text corpus for reference",
            "Another document in the corpus",
            "More documents..."
        ]
        tfidf_scores = calculate_tfidf_on_phrases(processed_text, corpus)

        # Step 6: Combine NER and TF-IDF results
        combined_results = combine_nlp_results(tfidf_scores, ner_mapped)

        # Prepare mapped topics
        mapped_topics = [
            [k, v['uri'][0]] for k, v in combined_results.items() if v['uri']
        ]

        return {
            "topics_with_weights": combined_results,
            "mapped_topics": mapped_topics
        }
    except ValueError as ve:
        logger.error(f"ValueError during NER analysis: {ve}")
        return {
            "topics_with_weights": {},
            "mapped_topics": [],
            "error": str(ve)
        }
    except Exception as e:
        logger.error(f"Error during NER analysis: {e}", exc_info=True)
        return {
            "topics_with_weights": {},
            "mapped_topics": [],
            "error": f"Error during NER analysis: {str(e)}"
        }
