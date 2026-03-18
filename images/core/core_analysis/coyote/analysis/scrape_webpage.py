# scrape_webpage.py

import logging
from typing import List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
import chardet

logger = logging.getLogger(__name__)

# SSRF Protection: Only allow http/https schemes
ALLOWED_SCHEMES = frozenset({'http', 'https'})

def _validate_url(url: str) -> bool:
    """
    Validate URL to prevent SSRF attacks.

    Only allows http/https schemes. Blocks file://, gopher://, etc.
    Note: Does not block internal IPs since users may legitimately
    browse local network resources.

    Args:
        url: The URL to validate

    Returns:
        True if URL is safe to fetch, False otherwise
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme.lower() not in ALLOWED_SCHEMES:
            logger.warning(f"Blocked URL with disallowed scheme: {parsed.scheme}")
            return False
        if not parsed.netloc:
            logger.warning(f"Blocked URL with no host: {url}")
            return False
        return True
    except Exception as e:
        logger.warning(f"URL validation error for {url}: {e}")
        return False

def get_main_content_text(
    soup: BeautifulSoup,
    semantic_tags: Optional[List[str]] = None,
    content_ids_classes: Optional[List[str]] = None
) -> str:
    """
    Extract the main content text from a BeautifulSoup object by looking for
    common semantic HTML elements and divs with common content IDs or classes.

    Args:
        soup (BeautifulSoup): The BeautifulSoup object representing the parsed HTML.
        semantic_tags (Optional[List[str]]): List of semantic HTML tags to search for.
            Defaults to ['main', 'article', 'section'].
        content_ids_classes (Optional[List[str]]): List of common content IDs or
            classes to search for. Defaults to ['content', 'post', 'text', 'article'].

    Returns:
        str: The extracted main content text from the webpage.
    """
    if semantic_tags is None:
        semantic_tags = ['main', 'article', 'section']

    if content_ids_classes is None:
        content_ids_classes = ['content', 'post', 'text', 'article']

    main_content = []

    # Search for common semantic HTML elements
    for tag in semantic_tags:
        contents = soup.find_all(tag)
        for content in contents:

            if content:
                text = content.get_text(separator=' ', strip=True)
                main_content.append(text)
                logger.debug(f"Found content in <{tag}> tag: {text[:100]}...")

    # Search for divs with common content IDs or classes
    for id_or_class in content_ids_classes:
        contents = soup.find_all(class_=id_or_class)
        contents += soup.find_all(id=id_or_class)
        for content in contents:
            if content:
                text = content.get_text(separator=' ', strip=True)
                main_content.append(text)
                logger.debug(f"Found content with id/class '{id_or_class}': {text[:100]}...")

    # Combine all gathered text
    if main_content:
        combined_text = ' '.join(main_content)
        logger.debug("Combined main content text.")

        return combined_text

    # As a last resort, return the entire body text if it exists
    if soup.body:
        body_text = soup.body.get_text(separator=' ', strip=True)
        logger.debug("Returning entire body text as main content.")

        return body_text

    logger.debug("No content found in the webpage.")
    return ""

def scrape_webpage(url: str) -> tuple[str, str]:
    """
    Scrape the title and main text content from a webpage.

    Args:
        url (str): The URL of the webpage to scrape.

    Returns:
        tuple[str, str]: (title, main_content) - The page title and extracted text,
            or empty strings if an error occurs or URL is invalid.
    """
    # SSRF protection: validate URL before fetching
    if not _validate_url(url):
        logger.error(f"Refused to fetch invalid/unsafe URL: {url}")
        return "", ""

    try:
        # Make a request to the given URL
        response = requests.get(url, timeout=10)

        response.raise_for_status()  # Raise an HTTPError for bad responses

        # Detect the encoding of the response content
        detected_encoding = chardet.detect(response.content)
        encoding = detected_encoding['encoding'] or 'utf-8'
        logger.debug(f"Detected encoding: {encoding}")

        # If encoding is None, default to 'utf-8'
        if not encoding:
            encoding = 'utf-8'
            logger.debug("Encoding not detected, defaulting to 'utf-8'.")

        # Parse the page content with BeautifulSoup
        soup = BeautifulSoup(response.content, 'html.parser', from_encoding=encoding)

        # Extract title
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        elif soup.find('h1'):
            # Fallback to first H1 if no title tag
            title = soup.find('h1').get_text(strip=True)
        logger.debug(f"Extracted title: {title}")

        # Extract the main text content from the page
        webpage_text = get_main_content_text(soup)
        logger.debug(f"Extracted text content length: {len(webpage_text)} characters.")

        return title, webpage_text
    except requests.RequestException as e:
        logger.error(f"Error scraping webpage at {url}: {e}")

        return "", ""
    except Exception as e:
        logger.error(f"Unexpected error during scraping at {url}: {e}")
        
        return "", ""

def should_exempt_url(url: str) -> bool:
    """
    Check if a URL should be exempt from NLP processing.

    This includes:
    - Google SERPs
    - Hypothes.is account, users, and oauth pages
    - The local configure page
    """
    # Original Google SERP check
    if "google.com/search" in url:
        return True

    # Hypothes.is pages to exempt
    if url.startswith("https://hypothes.is/account"):
        return True
    if url.startswith("https://hypothes.is/users"):
        return True
    if url.startswith("https://hypothes.is/oauth"):
        return True

    # Local configure page
    if url.startswith("http://localhost:5000/configure"):
        return True

    return False
