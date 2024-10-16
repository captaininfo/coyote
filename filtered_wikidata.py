import rdflib
import logging
import os

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Define the properties you are interested in
properties = {
    "P31": rdflib.URIRef("http://www.wikidata.org/prop/direct/P31"),
    "P279": rdflib.URIRef("http://www.wikidata.org/prop/direct/P279"),
    "P361": rdflib.URIRef("http://www.wikidata.org/prop/direct/P361"),
    "P527": rdflib.URIRef("http://www.wikidata.org/prop/direct/P527"),
    "P155": rdflib.URIRef("http://www.wikidata.org/prop/direct/P155"),
    "P156": rdflib.URIRef("http://www.wikidata.org/prop/direct/P156"),
    "P910": rdflib.URIRef("http://www.wikidata.org/prop/direct/P910")
}

# Function to process RDF data in chunks and filter it
def process_rdf_data_in_chunks(input_file_path, output_file_path, chunk_size=100000):
    count = 0

    with open(input_file_path, "r") as file:
        with open(output_file_path, "a") as output_file:  # Open the output file in append mode
            for line in file:
                try:
                    triple = rdflib.Graph().parse(data=line, format="nt").triples((None, None, None)).__next__()
                    subj, pred, obj = triple

                    if pred in properties.values():
                        output_file.write(f"{subj.n3()} {pred.n3()} {obj.n3()} .\n")
                        count += 1

                    if count % chunk_size == 0:
                        logger.info(f"Processed {count} triples so far...")

                except Exception as e:
                    logger.warning(f"Skipping line due to error: {e}")

    logger.info(f"Finished processing. Total triples written: {count}")

# Specify the absolute path to the file on your external hard drive
input_file_path = "/media/justin/Seagate_2TB/latest-truthy.nt"
output_file_path = "/media/justin/Seagate_2TB/filtered_wikidata.nt"

# Ensure the output file is cleared before starting
if os.path.exists(output_file_path):
    os.remove(output_file_path)

# Process the RDF data
if os.path.isfile(input_file_path):
    process_rdf_data_in_chunks(input_file_path, output_file_path)
else:
    logger.error(f"The file at {input_file_path} does not exist. Please check the path and try again.")
