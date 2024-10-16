import spacy
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Load SpaCy model (ensure 'en_core_web_sm' is installed: python -m spacy download en_core_web_sm)
nlp = spacy.load("en_core_web_sm")

def extract_entities(text):
    """
    Extract named entities from the given text using SpaCy.

    Args:
        text (str): The text to analyze.

    Returns:
        list: List of tuples containing entities and their labels.
    """
    try:
        doc = nlp(text)
        entities = [(ent.text, ent.label_) for ent in doc.ents]
        # logging.debug(f"Extracted Entities: {entities}")
        return entities
    except Exception as e:
        logging.error(f"Error in extract_entities: {e}")
        return []

# Example usage
# if __name__ == "__main__":
#     sample_text = "Barack Obama was born in Hawaii."
#     print(extract_entities(sample_text))
