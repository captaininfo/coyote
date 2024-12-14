import logging
from rake_nltk import Rake
from typing import Any, Dict

# Get logger
logger = logging.getLogger(__name__)

def extract_topics_with_rake(self, text: str) -> Dict[str, Any]:
    """
    Extracts topics from text using RAKE.

    Args:
        text (str): The text to analyze.

    Returns:
        Dict[str, Any]: Dictionary containing extracted topics and their scores.
    """
    try:
        # Initialize RAKE with desired parameters or default settings
        rake = Rake()  # Ensure Rake is imported and initialized appropriately
        
        # Extract keywords/phrases from the text
        rake.extract_keywords_from_text(text)
        
        # Retrieve ranked phrases with their corresponding scores
        ranked_phrases_with_scores = rake.get_ranked_phrases_with_scores()
        logger.debug(f"Ranked Phrases with Scores: {ranked_phrases_with_scores}")
        
        # ---- Commented Out: Normalize scores and limit results ----
        # # Normalize scores by the total of top N scores
        # total = sum(score for score, phrase in ranked_phrases_with_scores[:10])  # Limit to top 10
        # 
        # # Create a list of tuples containing phrases and their normalized scores
        # topics_with_weights = [
        #     (phrase, score / total if total else 0) 
        #     for score, phrase in ranked_phrases_with_scores[:10]
        # ]
        # ----------------------------------------------------------
        
        # For now, return all extracted phrases with their original scores without normalization or limiting
        topics_with_weights = [
            (phrase, score) for score, phrase in ranked_phrases_with_scores
        ]
        
        logger.debug(f"Topics with Weights (Unnormalized & Unlimited): {topics_with_weights}")
        
        return {"topics_with_weights": topics_with_weights}
    
    except Exception as e:
        logger.error(f"Error extracting topics with RAKE: {e}", exc_info=True)
        return {"topics_with_weights": []}
