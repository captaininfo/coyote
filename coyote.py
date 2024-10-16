import json
import logging
from scrape_webpage import scrape_webpage
from summarize_text import summarize_text
from text_ner_analysis import get_ner_from_text
from text_bertopic_analysis import get_topic_from_text
from relevance_calculator import calculate_relevance

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

def append_to_json_file(file_path, data):
    try:
        with open(file_path, "r+") as file:
            file_data = json.load(file)
            file_data.append(data)
            file.seek(0)
            json.dump(file_data, file, indent=4)
    except FileNotFoundError:
        with open(file_path, "w") as file:
            json.dump([data], file, indent=4)
    except Exception as e:
        print(f"Failed to append data to {file_path}: {e}")

def process_hypothesis_annotations(annotations):
    for annotation in annotations:
        text = annotation.get('text', '')
        topics_data = get_topic_from_text(text) if text else {"topics_with_weights": {}, "mapped_topics": []}
        ner_data = get_ner_from_text(text) if text else {"topics_with_weights": {}, "mapped_topics": []}
        highlighted_text = "".join([sel.get('exact', '') for sel in annotation['target'][0].get('selector', []) if sel.get('type') == 'TextQuoteSelector'])
        highlighted_topics_data = get_topic_from_text(highlighted_text) if highlighted_text else {"topics_with_weights": {}, "mapped_topics": []}
        highlighted_ner_data = get_ner_from_text(highlighted_text) if highlighted_text else {"topics_with_weights": {}, "mapped_topics": []}
        annotation_data = {
            "timestamp": annotation['created'],
            "event": "User annotated webpage",
            "dataSource": "Hypothesis",
            "url": annotation['uri'],
            "webpageTitle": annotation['document']['title'][0] if annotation['document'].get('title') else '',
            "annotationID": annotation['id'],
            "annotationText": text,
            "annotationTextTopics": topics_data["mapped_topics"],
            "annotationTextEntities": ner_data["mapped_topics"],
            "highlightedText": highlighted_text,
            "highlightedTextTopics": highlighted_topics_data["mapped_topics"],
            "highlightedTextEntities": highlighted_ner_data["mapped_topics"],
            "tags": annotation.get('tags', []),
            "userAccount": annotation['user'],
            "group": annotation['group'],
            "visibility": "public" if "group:__world__" in annotation['permissions']['read'] else "private"
        }
        append_to_json_file("analysis_result.json", annotation_data)

def is_google_serp(url):
    return "google.com/search" in url

def process_data_from_server(data):
    if data.get('event') == "User annotated webpage":
        process_hypothesis_annotations(data['annotations'])
    else:
        results = {"timestamp": data['timestamp'], 
                   "event": data.get('event'), 
                   "dataSource": data.get('dataSource', 'Coyote Browser Extension')}
        try:
            if data['event'] == 'User starts or modifies a search':
                purpose_topics_data = get_topic_from_text(data['purpose'])
                purpose_ner_data = get_ner_from_text(data['purpose'])
                search_terms_topics_data = get_topic_from_text(data['searchTerms'])
                search_terms_ner_data = get_ner_from_text(data['searchTerms'])
                results.update({
                    "purpose": data['purpose'], 
                    "purposeTopics": purpose_topics_data["mapped_topics"],
                    "purposeEntities": purpose_ner_data["mapped_topics"],
                    "searchTerms": data['searchTerms'], 
                    "searchTermsTopics": search_terms_topics_data["mapped_topics"],
                    "searchTermsEntities": search_terms_ner_data["mapped_topics"],
                    "searchTerms_relevanceScores": calculate_relevance(purpose_topics_data["topics_with_weights"], search_terms_topics_data["topics_with_weights"])
                })

            elif data['event'] == 'Webpage loads':
                url = data['url']
                if is_google_serp(url):
                    # Skip NLP analysis for Google SERP pages
                    results.update({
                        "url": url,
                        "webpageTitle": data.get('title'),
                    })
                else:
                    webpage_text = scrape_webpage(url)
                    summary = summarize_text(webpage_text)
                    topics_data = get_topic_from_text(webpage_text)
                    summary_topics_data = get_topic_from_text(summary)
                    ner_data = get_ner_from_text(webpage_text)
                    results.update({
                        "url": url, 
                        "webpageTitle": data.get('title'), 
                        "webpageSummary": summary, 
                        "webpageTopics": [{"topic": k, "uri": v['uri'], "score": v['score']} for k, v in topics_data["topics_with_weights"].items()],
                        "webpageNamedEntities": [{"entity": k, "uri": v['uri'], "score": v['score']} for k, v in ner_data["topics_with_weights"].items()],
                        "webpage_relevanceScores": calculate_relevance(topics_data["topics_with_weights"], summary_topics_data["topics_with_weights"])
                    })

            elif data['event'] == 'user clicks hyperlink':
                hyperlink_topics_data = get_topic_from_text(data['linkText'])
                hyperlink_ner_data = get_ner_from_text(data['linkText'])
                results.update({
                    "sourceURL": data['sourceURL'], 
                    "destinationURL": data['destinationURL'], 
                    "linkText": data['linkText'], 
                    "hyperlinkTopics": [{"topic": k, "uri": v['uri'], "score": v['score']} for k, v in hyperlink_topics_data["topics_with_weights"].items()],
                    "hyperlinkEntities": [{"entity": k, "uri": v['uri'], "score": v['score']} for k, v in hyperlink_ner_data["topics_with_weights"].items()]
                })

            append_to_json_file("analysis_result.json", results)
            return {"status": "success", "message": "Data processed and stored."}

        except Exception as e:
            logging.error(f"Error processing data: {e}")
            return {"status": "error", "message": str(e)}

def main():
    print("Coyote V2 is running...")

if __name__ == "__main__":
    main()
