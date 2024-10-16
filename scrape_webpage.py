# scrape_webpage.py

import requests
from bs4 import BeautifulSoup
import logging
import chardet

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

def get_main_content_text(soup):
    """
    Extracts the main content text from a BeautifulSoup object by looking for common semantic HTML elements and 
    divs with common content IDs or classes.
    """
    main_content = []

    # Common semantic HTML elements
    for tag in ['main', 'article', 'section']:
        contents = soup.find_all(tag)
        for content in contents:
            if content:
                main_content.append(content.get_text(separator=' ', strip=True))

    # Divs with common content IDs or classes
    for id_or_class in ['content', 'post', 'text', 'article']:
        contents = soup.find_all(class_=id_or_class) or soup.find_all(id=id_or_class)
        for content in contents:
            main_content.append(content.get_text(separator=' ', strip=True))

    # Combine all gathered text
    if main_content:
        return ' '.join(main_content)

    # As a last resort, return the entire body text if it exists
    return soup.body.get_text(separator=' ', strip=True) if soup.body else ""

def scrape_webpage(url):
    try:
        # Make a request to the given URL
        response = requests.get(url)
        response.raise_for_status()  # Raise an HTTPError for bad responses (4xx and 5xx)

        # Detect the encoding of the response content
        detected_encoding = chardet.detect(response.content)
        encoding = detected_encoding['encoding']
        logging.debug(f"Detected encoding: {encoding}")

        # Parse the page content with BeautifulSoup
        soup = BeautifulSoup(response.content, 'html.parser', from_encoding=encoding)

        # Extract the main text content from the page
        webpage_text = get_main_content_text(soup)
        logging.debug(f"Extracted text content: {webpage_text[:1000]}...")  # Print the first 1000 characters for debugging
        return webpage_text
    except requests.RequestException as e:
        logging.error(f"Error scraping webpage: {e}")
        return ""
    except Exception as e:
        logging.error(f"Unexpected error during scraping: {e}")
        return ""

# Example usage
# webpage_text = scrape_webpage("http://example.com")
# print(webpage_text)
