import json
import sqlite3
import os

DB_PATH = os.path.join('../../data', 'coyote_event_data.db')
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

with open('cleaned_transcript_data.json', 'r', encoding='utf-8') as f:
    talks = json.load(f)

for talk in talks:
    title = talk.get('title', '')
    content = talk.get('cleaned_transcript', '')
    cursor.execute("INSERT INTO CorpusDocuments (title, content, source) VALUES (?, ?, ?)",
                   (title, content, 'TEDTalk'))
conn.commit()
conn.close()
