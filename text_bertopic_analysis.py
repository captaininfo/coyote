import logging
from nltk.corpus import stopwords
from sklearn.feature_extraction.text import TfidfVectorizer
from SPARQLWrapper import SPARQLWrapper, JSON
from bertopic_analysis import analyze_topics
import spacy

# Load SpaCy model (ensure 'en_core_web_sm' is installed: python -m spacy download en_core_web_sm)
nlp = spacy.load("en_core_web_sm")

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Custom list of domain-specific stopwords
custom_stopwords = [
    'page', 'click', 'link', 'comment', 'username', 'password', 'login',
    'subscribe', 'share', 'like', 'read', 'more', 'article', 'posted', 'said'
]

# Combine with standard stopwords as a list
stop_words_list = list(set(stopwords.words('english')).union(set(custom_stopwords)))

def query_wikidata(term):
    try:
        sparql = SPARQLWrapper("https://query.wikidata.org/sparql")
        query = f"""
        SELECT ?item ?itemLabel WHERE {{
            ?item ?label "{term}"@en.
            FILTER (STRSTARTS(STR(?item), "http://www.wikidata.org/entity/Q"))
            SERVICE wikibase:label {{ bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en". }}
        }}
        LIMIT 1
        """
        sparql.setQuery(query)
        sparql.setReturnFormat(JSON)
        results = sparql.query().convert()
        return [(result['itemLabel']['value'], result['item']['value']) for result in results['results']['bindings']]
    except Exception as e:
        logging.error(f"Error querying WikiData for term '{term}': {e}")
        return []

def map_topics_to_wikidata(topics):
    try:
        mapped_topics = {}
        for topic, words in topics.items():
            for word, _ in words:
                wikidata_result = query_wikidata(word)
                if (wikidata_result):
                    mapped_topics[word] = {'uri': wikidata_result[0][1]}
        logging.debug(f"Mapped Topics to WikiData: {mapped_topics}")
        return mapped_topics
    except Exception as e:
        logging.error(f"Error in map_topics_to_wikidata: {e}")
        return {}

def extract_and_replace_topics(text, topics_mapped):
    for topic, data in topics_mapped.items():
        replacement = topic.replace(" ", "_")
        text = text.replace(topic, replacement)
    return text

def calculate_tfidf_on_phrases(text, corpus):
    try:
        vectorizer = TfidfVectorizer(stop_words=stop_words_list)
        tfidf_matrix = vectorizer.fit_transform(corpus + [text])
        feature_names = vectorizer.get_feature_names_out()
        scores = {feature_names[i]: tfidf_matrix[-1, i] for i in tfidf_matrix[-1].nonzero()[1] if tfidf_matrix[-1, i] > 0.07}  # Adjusted threshold to 0.07
        logging.debug(f"TF-IDF Scores on Phrases: {scores}")
        return scores
    except Exception as e:
        logging.error(f"Error in calculate_tfidf_on_phrases: {e}")
        return {}

def combine_nlp_results(tfidf_scores, topics_mapped):
    combined_results = {}
    for term, score in tfidf_scores.items():
        combined_results[term] = {'score': score, 'uri': [topics_mapped.get(term, {}).get('uri', '')]}

    for topic, data in topics_mapped.items():
        topic_key = topic.replace(" ", "_")
        combined_results[topic_key] = {'score': combined_results.get(topic_key, {}).get('score', 0), 'uri': [data['uri']]}
        combined_results[topic_key]['labels'] = data.get('label', 'UNKNOWN')

    logging.debug(f"Combined BERTopic Results: {combined_results}")
    return combined_results

def get_topic_from_text(text, verbose=True):
    try:
        if not text or 'error' in text.lower():
            raise ValueError("Text contains an error message or is empty")
        
        # Step 1: Remove stopwords
        processed_text = ' '.join([word for word in text.split() if word.lower() not in stop_words_list])

        # Step 2: Model topics with BERTopic
        topic_results = analyze_topics(processed_text)
        if not topic_results:
            raise ValueError("No topics extracted from the input text.")
        
        detailed_topics = topic_results['detailed_topics']
        logging.debug(f"Detailed Topics: {detailed_topics}")

        # Step 3: Map topics to WikiData
        topics_mapped = map_topics_to_wikidata(detailed_topics)

        # Step 4: Replace topics in text
        processed_text = extract_and_replace_topics(processed_text, topics_mapped)
        logging.debug(f"Processed Text: {processed_text}")
        
        # Step 5: Calculate TF-IDF scores
        tfidf_scores = calculate_tfidf_on_phrases(processed_text, ["sample text corpus for reference", "another document in the corpus", "more documents..."])
        
        # Step 6: Combine NER and TF-IDF results
        combined_results = combine_nlp_results(tfidf_scores, topics_mapped)
        
        return {"topics_with_weights": combined_results, "mapped_topics": [[k, v['uri'][0]] for k, v in combined_results.items() if v['uri']]}
    except ValueError as ve:
        logging.error(f"ValueError during topic modeling: {ve}")
        return {"topics_with_weights": {}, "mapped_topics": [], "error": str(ve)}
    except Exception as e:
        logging.error(f"Error during topic modeling: {str(e)}")
        return {"topics_with_weights": {}, "mapped_topics": [], "error": f"Error during topic modeling: {str(e)}"}

# Example usage
if __name__ == "__main__":
    text = "Your text here..."
    result = get_topic_from_text(text)
    print(result)
