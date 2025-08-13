from neo4j import GraphDatabase

def get_schema(uri:str, user:str, pwd:str) -> str:
    driver = GraphDatabase.driver(uri, auth=(user, pwd))
    with driver.session() as s:
        # APOC call is simplest
        res = s.run("CALL apoc.meta.schema() YIELD value RETURN value").single()
    driver.close()
    return str(res["value"])
