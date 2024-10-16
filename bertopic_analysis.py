# bertopic_analysis.py

import logging
from bertopic import BERTopic
import spacy

# Configure logging
logging.basicConfig(filename='bertopic_analysis.log', level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Load a spaCy model
nlp = spacy.load('en_core_web_sm')

# Add the Sentencizer component to the pipeline
if 'sentencizer' not in nlp.pipe_names:
    nlp.add_pipe('sentencizer', before='parser')

def analyze_topics(text):
    # Process the text
    doc = nlp(text)

    # Extract sentences
    sentences = [sent.text.strip() for sent in doc.sents if sent.text.strip()]
    #logging.debug(f"Extracted sentences: {sentences}")
    #logging.debug(f"Total sentences extracted: {len(sentences)}")

    # Verify sentences list is not empty
    if not sentences:
        logging.error("No sentences extracted from the input text.")
        raise ValueError("No sentences extracted from the input text.")

    # Use BERTopic to model the topics
    topic_model = BERTopic(min_topic_size=2, n_gram_range=(1, 3))
    try:
        topics, probs = topic_model.fit_transform(sentences)
        logging.debug(f"BERTopic Results: Topics: {topics}, Probabilities: {probs}")
        
        # Get topic information
        topic_info = topic_model.get_topic_info()
        logging.debug(f"Topic Info: {topic_info}")
        
        # Get detailed topics
        detailed_topics = {}
        for topic_num in topic_info['Topic']:
            if topic_num != -1:
                topic_details = topic_model.get_topic(topic_num)
                detailed_topics[topic_num] = topic_details
                logging.debug(f"Topic {topic_num} details: {topic_details}")
        
        return {"topic_info": topic_info, "detailed_topics": detailed_topics}
    
    except Exception as e:
        logging.error(f"Error during BERTopic processing: {e}", exc_info=True)
        return None, None
