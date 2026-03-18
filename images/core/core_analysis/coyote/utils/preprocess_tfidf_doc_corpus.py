import pandas as pd
import json
import nltk
import string
from nltk.corpus import stopwords
from nltk.stem import WordNetLemmatizer
from nltk import word_tokenize
import sys

# Step 1: Download necessary NLTK data packages
# Corrected from 'punkt_tab' to 'punkt'
nltk.download('punkt', quiet=True)
nltk.download('stopwords', quiet=True)
nltk.download('wordnet', quiet=True)
nltk.download('omw-1.4', quiet=True)  # For lemmatization

# Step 2: Initialize NLTK tools
stop_words = set(stopwords.words('english'))
lemmatizer = WordNetLemmatizer()

def clean_text(input_csv_path: str, output_json_path: str) -> None:
    """
    Reads a CSV file, cleans/pre-processes the 'transcript' column by converting text to lowercase,
    removing stop words, applying lemmatization, and then saves the cleaned data to a JSON file.
    
    Args:
        input_csv_path (str): Path to the input CSV file with 'title' and 'transcript' columns.
        output_json_path (str): Path where the output JSON file will be saved.
    """
    def process_single_text(text: str) -> str:
        """
        Cleans and pre-processes a single text string.
        
        Args:
            text (str): The original text to be cleaned.
            
        Returns:
            str: The cleaned text.
        """
        if pd.isnull(text):
            return ""
        
        # 1. Convert to lowercase
        text = text.lower()
        
        # 2. Tokenize text into words
        tokens = word_tokenize(text)
        
        # 3. Remove punctuation from each token
        table = str.maketrans('', '', string.punctuation)
        stripped_tokens = [word.translate(table) for word in tokens]
        
        # 4. Remove non-alphabetic tokens
        words = [word for word in stripped_tokens if word.isalpha()]
        
        # 5. Remove stop words
        words = [word for word in words if word not in stop_words]
        
        # 6. Apply lemmatization
        lemmatized_words = [lemmatizer.lemmatize(word) for word in words]
        
        # 7. Join words back into a single string
        cleaned_text = ' '.join(lemmatized_words)
        
        return cleaned_text

    # Step 3: Read the CSV file into a pandas DataFrame
    try:
        df = pd.read_csv(input_csv_path)
        print(f"Successfully loaded '{input_csv_path}'.")
    except FileNotFoundError:
        print(f"Error: The file '{input_csv_path}' was not found.")
        sys.exit(1)
    except pd.errors.EmptyDataError:
        print(f"Error: The file '{input_csv_path}' is empty.")
        sys.exit(1)
    except pd.errors.ParserError:
        print(f"Error: The file '{input_csv_path}' does not appear to be in CSV format.")
        sys.exit(1)
    
    # Verify that the necessary columns exist
    expected_columns = {'title', 'transcript'}
    if not expected_columns.issubset(df.columns):
        print(f"Error: The CSV file must contain the following columns: {expected_columns}")
        sys.exit(1)
    
    # Step 4: Apply the cleaning function to the 'transcript' column
    print("Starting text cleaning...")
    df['cleaned_transcript'] = df['transcript'].apply(process_single_text)
    print("Text cleaning completed.")
    
    # Step 5: Drop the original 'transcript' column to reduce JSON size
    df = df.drop(columns=['transcript'])
    
    # Step 6: Convert the DataFrame to JSON format with only 'title' and 'cleaned_transcript'
    print(f"Converting cleaned data to JSON format and saving to '{output_json_path}'...")
    try:
        # Using 'records' orientation and writing JSON lines for better scalability
        df.to_json(output_json_path, orient='records', force_ascii=False, indent=4)
        print(f"Successfully saved cleaned data to '{output_json_path}'.")
    except Exception as e:
        print(f"Error while saving JSON file: {e}")
        sys.exit(1)

def main():
    # Specify the path to your CSV file
    input_csv_path = 'transcript_data.csv'  # Replace with your actual file path
    output_json_path = 'cleaned_transcript_data.json'  # Desired output JSON file path
    
    # Call the clean_text function
    clean_text(input_csv_path, output_json_path)

if __name__ == "__main__":
    main()
