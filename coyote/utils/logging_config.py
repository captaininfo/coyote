import logging
import sys

def setup_logging():
    logging.basicConfig(
        level=logging.DEBUG,  # Adjust the level as needed (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)  # Output logs to stdout
        ]
    )
