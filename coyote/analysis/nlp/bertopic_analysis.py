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

    Args:
        text (str): The input text to analyze.

    Returns:
        Tuple[Optional[Dict[str, Any]], Optional[List[Tuple[str, float]]]]:
            A tuple containing the topic information and a list of detailed topics.
            Returns (None, None) if an error occurs.
    """
    if nlp is None:
        logger.error("spaCy model is not initialized.")
        return None, None

    try:
        # Process the text
        doc = nlp(text)

        # Extract sentences
        sentences = [sent.text.strip() for sent in doc.sents if sent.text.strip()]
        logger.debug(f"Extracted {len(sentences)} sentences.")

        # Verify sentences list is not empty
        if not sentences:
            logger.error("No sentences extracted from the input text.")
            raise ValueError("No sentences extracted from the input text.")

        # Use BERTopic to model the topics
        topic_model = BERTopic(min_topic_size=2, n_gram_range=(1, 3))
        topics, probs = topic_model.fit_transform(sentences)
        logger.debug(f"BERTopic Results: Topics: {topics}, Probabilities: {probs}")

        # Get topic information
        topic_info = topic_model.get_topic_info()
        logger.debug(f"Topic Info: {topic_info}")

        # Get detailed topics as a flat list
        detailed_topics = []
        for topic_num in topic_info['Topic']:
            if topic_num != -1:
                topic_details = topic_model.get_topic(topic_num)
                detailed_topics.extend(topic_details)  # Append all (topic_str, topic_score) tuples
                logger.debug(f"Topic {topic_num} details: {topic_details}")

        return topic_info, detailed_topics

    except Exception as e:
        logger.exception(f"Error during topic analysis: {e}")
        return None, None

