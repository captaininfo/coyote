# bertopic_analysis.py

import logging
from typing import Dict, Any, List, Tuple, Optional

from bertopic import BERTopic
import spacy

logger = logging.getLogger(__name__)

# Load the spaCy model once at the module level
try:
    nlp = spacy.load('en_core_web_sm')
    # Add the Sentencizer component to the pipeline if not present
    if 'sentencizer' not in nlp.pipe_names:
        nlp.add_pipe('sentencizer', before='parser')
except Exception as e:
    logger.error(f"Failed to load spaCy model: {e}")
    nlp = None  # Handle initialization failure

def analyze_topics(text: str) -> Tuple[Optional[Dict[str, Any]], Optional[List[Tuple[str, float]]]]:
    """
    Analyze topics in the given text using BERTopic.
    On small inputs (<4 sentences) or on any BERTopic/UMAP error
    the function returns ``(None, [])`` so the caller can fall back
    to RAKE without crashing the pipeline.

    Args:
        text (str): The input text to analyze.

    Returns:
        Tuple[Optional[Dict[str, Any]], Optional[List[Tuple[str, float]]]]:
            A tuple containing the topic information and a list of detailed topics.
            Returns (None, None) if an error occurs.
    """
    if nlp is None:
        logger.error("spaCy model is not initialized.")
        return None, []


    # 1) Sentence splitting -------------------------------------------------
    doc = nlp(text)
    sentences = [s.text.strip() for s in doc.sents if s.text.strip()]
    logger.debug("BERTopic - extracted %d sentences.", len(sentences))

    # Fallback for very short inputs
    if len(sentences) < 4:                      # ← tweak threshold here
        logger.info("Only %d sentence(s) – skipping BERTopic, will use RAKE.",
                    len(sentences))
        return None, []

    # 2) BERTopic -----------------------------------------------------------
    try:
        topic_model = BERTopic(min_topic_size=2, n_gram_range=(1, 3))
        topics, _ = topic_model.fit_transform(sentences)

        topic_info = topic_model.get_topic_info()

        detailed: List[Tuple[str, float]] = []
        for t_id in topic_info["Topic"]:
            if t_id != -1:                      # exclude outliers
                detailed.extend(topic_model.get_topic(t_id))

        return topic_info, detailed

    # 3) Any crash inside BERTopic → fallback --------------------------------
    except Exception as e:
        logger.exception("BERTopic failed – falling back to RAKE: %s", e)
        return None, []

