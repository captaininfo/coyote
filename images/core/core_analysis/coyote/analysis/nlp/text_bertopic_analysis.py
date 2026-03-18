"""
text_bertopic_analysis.py

Module for extracting topics from text using BERTopic and TF-IDF analysis,
and mapping them to WikiData entities.
"""

import logging, json, re
from typing import List, Dict, Any, Optional, Tuple

import spacy
from nltk.corpus import stopwords
from sklearn.feature_extraction.text import TfidfVectorizer
from SPARQLWrapper import SPARQLWrapper, JSON, SPARQLExceptions
import time, random

from coyote.analysis.nlp.bertopic_analysis import analyze_topics

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF     = (1.0, 3.0)   # seconds


def _escape_sparql_literal(raw: str) -> str:
    """
    Make *raw* safe for insertion between double quotes in a SPARQL query.

    • use json.dumps() to get proper back-slash escaping of quotes, control chars …
    • strip the surrounding pair of quotes added by json.dumps()
    • drop line-breaks and excessive whitespace (SPARQL literals cannot span lines)
    • truncate to some sane length to avoid DoS-size queries
    """
    safe = json.dumps(raw)[1:-1]          #  → \" and other escapes
    safe = re.sub(r"\s+", " ", safe)      # collapse \n, \t … into spaces
    return safe[:250]                     # hard cap – adjust as you like


# Load spaCy model
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


def query_wikidata(term: str) -> List[Tuple[str, str]]:
    """
    Query WikiData for *term* and return [(label, uri), …].
    The term is escaped so that quotes, back-slashes or line-breaks
    cannot break the SPARQL syntax.

    Args:
        term (str): The term to query.

    Returns:
        List[Tuple[str, str]]: A list of tuples containing the item label and item URI.
    """
    try:
        sparql = SPARQLWrapper("https://query.wikidata.org/sparql")
        # Wikidata blocks generic clients, so identify yourself
        sparql.agent = (
            "Coyote/0.3 (https://github.com/captaininfo/coyote; "
            "mailto:lifewidelearningllc@gmail.com)"
        )

        safe_term = _escape_sparql_literal(term)

        sparql.setQuery(f"""
            SELECT ?item ?itemLabel WHERE {{
                ?item ?label "{safe_term}"@en .
                FILTER (STRSTARTS(STR(?item), "http://www.wikidata.org/entity/Q"))
                SERVICE wikibase:label {{ bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en". }}
            }}
            LIMIT 1
        """)
        sparql.setReturnFormat(JSON)

        results = sparql.query().convert()
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                results = sparql.query().convert()
                break                                 # success
            except SPARQLExceptions.EndPointInternalError as e:
                # 5xx errors from the service
                logger.warning(
                    "WikiData 5xx on attempt %d/%d for '%s': %s",
                    attempt, MAX_RETRIES, term, e,
                )
            except Exception as e:
                if "403" not in str(e) and "429" not in str(e):
                    raise                               # real bug
                logger.warning(
                    "WikiData %s on attempt %d/%d for '%s'",
                    "403/429", attempt, MAX_RETRIES, term,
                )
            # back-off and retry
            time.sleep(random.uniform(*BACKOFF) * attempt)
        else:
            raise Exception("WikiData query failed after retries")
        
        return [
            (b['itemLabel']['value'], b['item']['value'])
            for b in results['results']['bindings']
        ]
    except Exception as e:
        logger.error(f"Error querying WikiData for term '{term}': {e}")
        return []


def map_topics_to_wikidata(topics: List[str]) -> Dict[str, Dict[str, str]]:
    """
    Map a list of topic strings to WikiData URIs.

    Args:
        topics (List[str]): A list of topic strings.

    Returns:
        Dict[str, Dict[str, str]]: Mapped topics with URIs and labels.
    """
    try:
        mapped_topics = {}
        for topic in topics:
            wikidata_result = query_wikidata(topic)
            if wikidata_result:
                label, uri = wikidata_result[0]  
                mapped_topics[topic] = {'uri': uri, 'label': label}
        logger.debug(f"Mapped Topics to WikiData: {mapped_topics}")
        return mapped_topics
    except Exception as e:
        logger.error(f"Error in map_topics_to_wikidata: {e}")
        return {}


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
            [word for word in text.split() if word.lower() not in stop_words_list]
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
