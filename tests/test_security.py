"""
Minimal security tests for Coyote MVP.

Tests the Cypher validation logic that prevents write operations
from being executed via LLM-generated queries.
"""
import pytest
import sys
from pathlib import Path

# Add shared module to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.nl2cypher import is_read_only, strip_fences_or_json, looks_like_cypher


class TestIsReadOnly:
    """Test the read-only Cypher validation blocklist."""

    # --- Should BLOCK these (return False) ---

    def test_blocks_create(self):
        assert is_read_only("CREATE (n:Node {name: 'test'})") is False

    def test_blocks_create_case_insensitive(self):
        assert is_read_only("create (n:Node)") is False
        assert is_read_only("Create (n:Node)") is False

    def test_blocks_merge(self):
        assert is_read_only("MERGE (n:Node {id: 1})") is False

    def test_blocks_delete(self):
        assert is_read_only("MATCH (n) DELETE n") is False

    def test_blocks_detach_delete(self):
        assert is_read_only("MATCH (n) DETACH DELETE n") is False

    def test_blocks_set(self):
        assert is_read_only("MATCH (n) SET n.prop = 'value'") is False

    def test_blocks_remove(self):
        assert is_read_only("MATCH (n) REMOVE n.prop") is False

    def test_blocks_drop(self):
        assert is_read_only("DROP INDEX my_index") is False

    def test_blocks_load_csv(self):
        assert is_read_only("LOAD CSV FROM 'file.csv' AS row") is False

    def test_blocks_load_csv_with_headers(self):
        assert is_read_only("LOAD CSV WITH HEADERS FROM 'file.csv' AS row") is False

    def test_blocks_apoc_load(self):
        assert is_read_only("CALL apoc.load.json('http://example.com/data.json')") is False

    def test_blocks_apoc_load_csv(self):
        assert is_read_only("CALL apoc.load.csv('file.csv')") is False

    def test_blocks_dbms_calls(self):
        assert is_read_only("CALL dbms.security.createUser('hacker', 'password')") is False

    def test_blocks_db_index_calls(self):
        assert is_read_only("CALL db.index.fulltext.createNodeIndex('idx', ['Node'], ['prop'])") is False

    # --- Should ALLOW these (return True) ---

    def test_allows_simple_match_return(self):
        assert is_read_only("MATCH (n) RETURN n") is True

    def test_allows_match_with_where(self):
        assert is_read_only("MATCH (n:Webpage) WHERE n.url CONTAINS 'example' RETURN n") is True

    def test_allows_match_with_relationship(self):
        assert is_read_only("MATCH (w:Webpage)-[:HAS_TOPIC]->(t) RETURN w, t") is True

    def test_allows_count(self):
        assert is_read_only("MATCH (n:Webpage) RETURN count(n)") is True

    def test_allows_with_clause(self):
        assert is_read_only("MATCH (n) WITH n LIMIT 10 RETURN n") is True

    def test_allows_order_by(self):
        assert is_read_only("MATCH (n:Webpage) RETURN n ORDER BY n.timestamp DESC") is True

    def test_allows_optional_match(self):
        assert is_read_only("MATCH (w:Webpage) OPTIONAL MATCH (w)-[:HAS_ANNOTATION]->(a) RETURN w, a") is True

    def test_allows_apoc_convert(self):
        # apoc.convert.* is allowed and used by Coyote
        assert is_read_only("MATCH (w:Webpage) RETURN apoc.convert.fromJsonList(w.topics)") is True

    def test_allows_apoc_meta(self):
        # apoc.meta.* is allowed and used by Coyote
        assert is_read_only("CALL apoc.meta.schema() YIELD value RETURN value") is True

    # --- Edge cases ---

    def test_handles_empty_string(self):
        assert is_read_only("") is True

    def test_handles_none(self):
        assert is_read_only(None) is True

    def test_handles_whitespace_variations(self):
        # Extra whitespace shouldn't bypass the check
        assert is_read_only("MATCH (n)   DELETE    n") is False


class TestStripFencesOrJson:
    """Test the LLM output sanitization."""

    def test_strips_cypher_code_fence(self):
        input_text = "```cypher\nMATCH (n) RETURN n\n```"
        assert strip_fences_or_json(input_text) == "MATCH (n) RETURN n"

    def test_strips_plain_code_fence(self):
        input_text = "```\nMATCH (n) RETURN n\n```"
        assert strip_fences_or_json(input_text) == "MATCH (n) RETURN n"

    def test_extracts_from_json(self):
        input_text = '{"cypher": "MATCH (n) RETURN n"}'
        assert strip_fences_or_json(input_text) == "MATCH (n) RETURN n"

    def test_handles_empty_string(self):
        assert strip_fences_or_json("") == ""

    def test_handles_plain_cypher(self):
        input_text = "MATCH (n) RETURN n"
        assert strip_fences_or_json(input_text) == "MATCH (n) RETURN n"


class TestLooksLikeCypher:
    """Test the Cypher detection heuristic."""

    def test_detects_match(self):
        assert looks_like_cypher("MATCH (n) RETURN n") is True

    def test_detects_call(self):
        assert looks_like_cypher("CALL apoc.meta.schema()") is True

    def test_detects_with(self):
        assert looks_like_cypher("WITH 1 AS x RETURN x") is True

    def test_rejects_plain_text(self):
        assert looks_like_cypher("What webpages did I visit?") is False

    def test_rejects_empty(self):
        assert looks_like_cypher("") is False

    def test_case_insensitive(self):
        assert looks_like_cypher("match (n) return n") is True
