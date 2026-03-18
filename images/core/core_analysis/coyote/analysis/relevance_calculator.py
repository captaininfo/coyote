# relevance_calculator.py

import math
import logging
from collections import Counter
from typing import Dict, Any

logger = logging.getLogger(__name__)

def cosine_similarity(vec1: Dict[Any, float], vec2: Dict[Any, float]) -> float:
    """
    Calculate the cosine similarity between two vectors.

    Args:
        vec1 (Dict[Any, float]): The first vector represented as a dictionary.
        vec2 (Dict[Any, float]): The second vector represented as a dictionary.

    Returns:
        float: The cosine similarity between vec1 and vec2.
    """
    intersection = set(vec1.keys()) & set(vec2.keys())
    numerator = sum(float(vec1[x]) * float(vec2[x]) for x in intersection)
    sum1 = sum(float(value) ** 2 for value in vec1.values())
    sum2 = sum(float(value) ** 2 for value in vec2.values())
    denominator = math.sqrt(sum1) * math.sqrt(sum2)
    if denominator == 0.0:
        logger.debug("Denominator is zero in cosine_similarity calculation.")
        return 0.0
    similarity = numerator / denominator
    logger.debug(f"Cosine similarity: {similarity}")
    return similarity

def flatten_scores(topics: Dict[str, Any]) -> Dict[str, float]:
    """
    Flatten the topics dictionary to extract scores.

    Args:
        topics (Dict[str, Any]): A dictionary containing topics and their details.

    Returns:
        Dict[str, float]: A dictionary mapping topics to their scores.
    """
    try:
        flattened = {
            key: float(value['score'])
            for key, value in topics.items()
            if isinstance(value, dict) and 'score' in value
        }
        logger.debug(f"Flattened topics: {flattened}")
        return flattened
    except Exception as e:
        logger.error(f"Error in flatten_scores: {e}")
        return {}

def calculate_relevance(topics1: Dict[str, Any], topics2: Dict[str, Any]) -> float:
    """
    Calculate the relevance between two sets of topics using cosine similarity.

    Args:
        topics1 (Dict[str, Any]): The first set of topics.
        topics2 (Dict[str, Any]): The second set of topics.

    Returns:
        float: The relevance score between the two topic sets.
    """
    try:
        vector1 = Counter(flatten_scores(topics1))
        vector2 = Counter(flatten_scores(topics2))
        relevance = cosine_similarity(vector1, vector2)
        logger.debug(f"Relevance score: {relevance}")
        return relevance
    except Exception as e:
        logger.error(f"Error in calculate_relevance: {e}")
        return 0.0
