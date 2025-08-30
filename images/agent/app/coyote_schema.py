from neo4j import GraphDatabase
import logging

log = logging.getLogger("coyote.agent")

def get_schema(uri:str, user:str, pwd:str) -> str:
    log.debug("get_schema uri=%s user=%s", uri, user)
    driver = GraphDatabase.driver(uri, auth=(user, pwd))
    with driver.session() as s:
        # APOC call is simplest
        res = s.run("CALL apoc.meta.schema() YIELD value RETURN value").single()
    driver.close()
    return str(res["value"])
