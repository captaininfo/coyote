"""
text_bertopic_analysis.py

RUMP MODULE. The BERTopic/TF-IDF topic path was replaced by KeyBERT in
Unit 3 (Phase 5); the WikiData lookup layer moved to
coyote.analysis.wikidata_lookup in Phase 4. All that survives here is
calculate_tfidf_on_phrases, still used by the entities scoring path
(coyote_nlp_state_manager Step 19) until Unit 4 replaces it with
mention-frequency scoring. Unit 4 deletes this file.
"""

import logging
from typing import List, Dict

from sklearn.feature_extraction.text import TfidfVectorizer

from coyote.analysis.nlp.stopwords import STOP_WORDS

logger = logging.getLogger(__name__)


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
