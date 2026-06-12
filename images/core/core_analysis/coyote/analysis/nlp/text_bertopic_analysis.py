"""
text_bertopic_analysis.py

Module for extracting topics from text using BERTopic and TF-IDF analysis.

Unit 3 M2/M3 (2026-06-11) moved the WikiData lookup layer (circuit breaker,
SPARQL escaping, term cache, query_wikidata, map_topics_to_wikidata) to
coyote.analysis.wikidata_lookup. What remains here is the BERTopic/TF-IDF
topic path, which Unit 3's wiring phase replaces with KeyBERT; this file
then becomes a rump (calculate_tfidf_on_phrases only) until Unit 4
deletes it.
"""

import logging
from typing import List, Dict, Any, Optional

import spacy
from sklearn.feature_extraction.text import TfidfVectorizer

from coyote.analysis.nlp.bertopic_analysis import analyze_topics
from coyote.analysis.nlp.stopwords import STOP_WORDS
from coyote.analysis.wikidata_lookup import map_topics_to_wikidata

logger = logging.getLogger(__name__)

# Load spaCy model
try:
    nlp = spacy.load("en_core_web_sm")
except Exception as e:
    logger.error(f"Failed to load spaCy model: {e}")
    nlp = None  # Handle initialization failure


# DEPRECATED: last caller (state-manager Step 12) dies in Phase 5; delete in Phase 6.
def extract_and_replace_topics(text: str, topics_mapped: Dict[str, Dict[str, str]]) -> str:
    """
    Replace topics in text with underscores for multi-word topics.

    Args:
        text (str): The original text.
        topics_mapped (Dict[str, Dict[str, str]]): Mapped topics with URIs.

    Returns:
        str: The processed text with topics replaced.
    """
    for topic in topics_mapped.keys():
        replacement = topic.replace(" ", "_")
        text = text.replace(topic, replacement)
    return text


# DEPRECATED: remove after Unit 4 lands (M9) — entities call site still uses this.
def calculate_tfidf_on_phrases(
    text: str,
    corpus: List[str],
    threshold: float = 0.07
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
        vectorizer = TfidfVectorizer(stop_words=STOP_WORDS)
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


# DEPRECATED: only caller is get_topic_from_text below; delete in Phase 6 (M11).
def combine_nlp_results(
    tfidf_scores: Dict[str, float],
    topics_mapped: Dict[str, Dict[str, str]]
) -> Dict[str, Dict[str, Any]]:
    """
    Combine TF-IDF scores and mapped topics into a single result.

    Args:
        tfidf_scores (Dict[str, float]): TF-IDF scores for terms.
        topics_mapped (Dict[str, Dict[str, str]]): Mapped topics with URIs and labels.

    Returns:
        Dict[str, Dict[str, Any]]: Combined results with scores, URIs, and labels.
    """
    combined_results = {}
    for term, score in tfidf_scores.items():
        mapped_data = topics_mapped.get(term, {})
        combined_results[term] = {
            'score': score,
            'uri': [mapped_data.get('uri', '')],
            'labels': mapped_data.get('label', 'UNKNOWN')
        }

    for topic, data in topics_mapped.items():
        topic_key = topic.replace(" ", "_")
        if topic_key not in combined_results:
            combined_results[topic_key] = {
                'score': 0,
                'uri': [data['uri']],
                'labels': data.get('label', 'UNKNOWN')
            }
    logger.debug(f"Combined NLP Results: {combined_results}")
    return combined_results


# DEPRECATED: dead code, zero callers; delete in Phase 6 (M8).
def get_topic_from_text(
    text: str,
    corpus: Optional[List[str]] = None,
    threshold: float = 0.07
) -> Dict[str, Any]:
    """
    Extract topics from text using BERTopic and TF-IDF analysis.

    Args:
        text (str): The text to analyze.
        corpus (Optional[List[str]]): The corpus of documents for TF-IDF calculation.
            If None, a default corpus is used.
        threshold (float): The threshold for including terms based on TF-IDF score.

    Returns:
        Dict[str, Any]: A dictionary containing topics with weights and mapped topics.
    """
    try:
        if not text or 'error' in text.lower():
            raise ValueError("Text contains an error message or is empty")

        # Step 1: Remove stopwords
        processed_text = ' '.join(
            [word for word in text.split() if word.lower() not in STOP_WORDS]
        )

        # Step 2: Model topics with BERTopic
        topic_info, detailed_topics = analyze_topics(processed_text)
        if not detailed_topics:
            raise ValueError("No topics extracted from the input text.")

        logger.debug(f"Detailed Topics: {detailed_topics}")

        # Step 3: Map topics to WikiData
        topics_mapped = map_topics_to_wikidata(detailed_topics)

        # Step 4: Replace topics in text
        processed_text = extract_and_replace_topics(processed_text, topics_mapped)
        logger.debug(f"Processed Text after replacing topics: {processed_text}")

        # Step 5: Calculate TF-IDF scores
        if corpus is None:
            corpus = [
                "Sample text corpus for reference.",
                "Another document in the corpus.",
                "More documents..."
            ]
            # Note: Replace with a real corpus in production

        tfidf_scores = calculate_tfidf_on_phrases(processed_text, corpus, threshold)

        # Step 6: Combine NLP results
        combined_results = combine_nlp_results(tfidf_scores, topics_mapped)

        mapped_topics = [
            [k, v['uri'][0]] for k, v in combined_results.items() if v['uri'][0]
        ]

        return {
            "topics_with_weights": combined_results,
            "mapped_topics": mapped_topics
        }
    except ValueError as ve:
        logger.error(f"ValueError during topic modeling: {ve}")
        return {
            "topics_with_weights": {},
            "mapped_topics": [],
            "error": str(ve)
        }
    except Exception as e:
        logger.error(f"Error during topic modeling: {e}", exc_info=True)
        return {
            "topics_with_weights": {},
            "mapped_topics": [],
            "error": f"Error during topic modeling: {str(e)}"
        }
