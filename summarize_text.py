# summarize_text.py

import logging
from summarizer.bert import Summarizer

def summarize_text(text):
    try:
        if not text:
            raise ValueError("Input text is empty or None")
        model = Summarizer()
        summary = model(text, min_length=60, max_length=500)
        return summary
    except Exception as e:
        logging.error(f"Error during summarization: {e}")
        return "Error during summarization: " + str(e)

# Example usage
#if __name__ == "__main__":
#    import sys
#    if len(sys.argv) != 2:
#        print("Usage: python3 summarize_text.py <text>")
#        sys.exit(1)
#    text = sys.argv[1]
#    summary = summarize_text(text)
#    print(summary)
