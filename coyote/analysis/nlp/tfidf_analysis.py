# tfidf_analysis.py

from sklearn.feature_extraction.text import TfidfVectorizer

def calculate_tfidf(corpus):
    vectorizer = TfidfVectorizer()
    tfidf_matrix = vectorizer.fit_transform(corpus)
    feature_names = vectorizer.get_feature_names_out()
    tfidf_scores = []
    for doc in range(len(corpus)):
        scores = {feature_names[col]: tfidf_matrix[doc, col] for col in tfidf_matrix[doc].nonzero()[1]}
        tfidf_scores.append(scores)
    return tfidf_scores
