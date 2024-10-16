import logging
from nltk.corpus import stopwords
from sklearn.feature_extraction.text import TfidfVectorizer
from SPARQLWrapper import SPARQLWrapper, JSON
from ner import extract_entities
import bertopic_analysis
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

def replace_named_entities_in_text(text, ner_mapped):
    for entity, data in ner_mapped.items():
        replacement = data['replacement']
        text = text.replace(entity, replacement)
    return text

def map_ner_to_wikidata(ner_entities):
    try:
        mapped_entities = {}
        for entity, label in ner_entities:
            wikidata_result = query_wikidata(entity)
            if wikidata_result:
                mapped_entities[entity] = {'replacement': entity.replace(" ", "_"), 'uri': wikidata_result[0][1], 'label': label}
        logging.debug(f"Mapped NER Entities to WikiData: {mapped_entities}")
        return mapped_entities
    except Exception as e:
        logging.error(f"Error in map_ner_to_wikidata: {e}")
        return {}

def calculate_tfidf_on_phrases(text, corpus):
    try:
        vectorizer = TfidfVectorizer(stop_words=stop_words_list)
        tfidf_matrix = vectorizer.fit_transform(corpus + [text])
        feature_names = vectorizer.get_feature_names_out()
        scores = {feature_names[i]: tfidf_matrix[-1, i] for i in tfidf_matrix[-1].nonzero()[1] if tfidf_matrix[-1, i] > 0.15}  # Adjusted threshold to 0.15
        logging.debug(f"TF-IDF Scores on Phrases: {scores}")
        return scores
    except Exception as e:
        logging.error(f"Error in calculate_tfidf_on_phrases: {e}")
        return {}

def map_tfidf_to_wikidata(tfidf_scores):
    try:
        wikidata_results = {term: query_wikidata(term) for term in tfidf_scores.keys()}
        logging.debug(f"WikiData Results: {wikidata_results}")
        return wikidata_results
    except Exception as e:
        logging.error(f"Error in map_tfidf_to_wikidata: {e}")
        return {}

def combine_nlp_results(tfidf_scores, ner_mapped):
    combined_results = {}
    for term, score in tfidf_scores.items():
        combined_results[term] = {'score': score, 'uri': [uri for label, uri in map_tfidf_to_wikidata({term: score}).get(term, [])]}

    for entity, data in ner_mapped.items():
        entity_key = data['replacement']
        combined_results[entity_key] = {'score': combined_results.get(entity_key, {}).get('score', 0), 'uri': [data['uri']]}
        combined_results[entity_key]['labels'] = data.get('label', 'UNKNOWN')

    logging.debug(f"Combined NER Results: {combined_results}")
    return combined_results

def get_ner_from_text(text, verbose=True):
    try:
        if not text or 'error' in text.lower():
            raise ValueError("Text contains an error message or is empty")
        
        # Step 1: Extract NER entities
        ner_entities = extract_entities(text)
        
        # Step 2: Map NER entities to WikiData
        ner_mapped = map_ner_to_wikidata(ner_entities)
        
        # Step 3: Replace NER entities in text with single-word equivalents
        processed_text = replace_named_entities_in_text(text, ner_mapped)
        logging.debug(f"Processed Text: {processed_text}")
        
        # Step 4: Remove stopwords
        processed_text = ' '.join([word for word in processed_text.split() if word.lower() not in stop_words_list])
        
        # Step 5: Calculate TF-IDF scores
        tfidf_scores = calculate_tfidf_on_phrases(processed_text, ["sample text corpus for reference", "another document in the corpus", "more documents..."])
        
        # Step 7: Combine NER and TF-IDF results
        combined_results = combine_nlp_results(tfidf_scores, ner_mapped)
        
        return {"topics_with_weights": combined_results, "mapped_topics": [[k, v['uri'][0]] for k, v in combined_results.items() if v['uri']]}
    except ValueError as ve:
        logging.error(f"ValueError during topic modeling: {ve}")
        return {"topics_with_weights": {}, "mapped_topics": [], "error": str(ve)}
    except Exception as e:
        logging.error(f"Error during topic modeling: {str(e)}")
        return {"topics_with_weights": {}, "mapped_topics": [], "error": f"Error during topic modeling: {str(e)}"}

# Example usage
#if __name__ == "__main__":
 #   text = "Your text here..."
  #  result = get_topic_from_text(text)
   # print(result)
