# relevance_calculator.py

import math
import logging
from collections import Counter

def cosine_similarity(vec1, vec2):
    intersection = set(vec1.keys()) & set(vec2.keys())
    numerator = sum([float(vec1[x]) * float(vec2[x]) for x in intersection])
    sum1 = sum([float(vec1[x])**2 for x in vec1.keys()])
    sum2 = sum([float(vec2[x])**2 for x in vec2.keys()])
    denominator = math.sqrt(sum1) * math.sqrt(sum2)
    return float(numerator) / denominator if denominator else 0.0

def flatten_scores(topics):
    try:
        flattened = {k: v['score'] for k, v in topics.items() if isinstance(v, dict) and 'score' in v}
        logging.debug(f"Flattened topics: {flattened}")
        return flattened
    except Exception as e:
        logging.error(f"Error in flatten_scores: {e}")
        return {}

def calculate_relevance(topics1, topics2):
    try:
        vector1 = Counter(flatten_scores(topics1))
        vector2 = Counter(flatten_scores(topics2))
        return cosine_similarity(vector1, vector2)
    except Exception as e:
        logging.error(f"Error in calculate_relevance: {e}")
        return 0.0
