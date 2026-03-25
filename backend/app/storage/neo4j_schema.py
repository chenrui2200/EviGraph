"""
Neo4j Schema — Cypher queries for index creation and schema management.
"""

from ..config import Config

# Constraints
CREATE_GRAPH_UUID_CONSTRAINT = """
CREATE CONSTRAINT graph_uuid IF NOT EXISTS
FOR (g:Graph) REQUIRE g.graph_id IS UNIQUE
"""

CREATE_ENTITY_UUID_CONSTRAINT = """
CREATE CONSTRAINT entity_uuid IF NOT EXISTS
FOR (n:Entity) REQUIRE n.uuid IS UNIQUE
"""

CREATE_EPISODE_UUID_CONSTRAINT = """
CREATE CONSTRAINT episode_uuid IF NOT EXISTS
FOR (ep:Episode) REQUIRE ep.uuid IS UNIQUE
"""

CREATE_DOCUMENT_UUID_CONSTRAINT = """
CREATE CONSTRAINT document_uuid IF NOT EXISTS
FOR (d:Document) REQUIRE d.uuid IS UNIQUE
"""

CREATE_PAGE_UUID_CONSTRAINT = """
CREATE CONSTRAINT page_uuid IF NOT EXISTS
FOR (p:Page) REQUIRE p.uuid IS UNIQUE
"""

# Regular indexes for fast filtering by graph_id
CREATE_ENTITY_GRAPH_ID_INDEX = """
CREATE INDEX entity_graph_id IF NOT EXISTS
FOR (n:Entity) ON (n.graph_id)
"""

CREATE_DOC_GRAPH_ID_INDEX = """
CREATE INDEX doc_graph_id IF NOT EXISTS
FOR (d:Document) ON (d.graph_id)
"""

CREATE_PAGE_GRAPH_ID_INDEX = """
CREATE INDEX page_graph_id IF NOT EXISTS
FOR (p:Page) ON (p.graph_id)
"""

CREATE_EPISODE_GRAPH_ID_INDEX = """
CREATE INDEX episode_graph_id IF NOT EXISTS
FOR (e:Episode) ON (e.graph_id)
"""

CREATE_EPISODE_SOURCE_INDEX = """
CREATE INDEX episode_source IF NOT EXISTS
FOR (e:Episode) ON (e.source)
"""

CREATE_EPISODE_CHUNK_INDEX = """
CREATE INDEX episode_chunk_index IF NOT EXISTS
FOR (e:Episode) ON (e.chunk_index)
"""

# Vector indexes (Neo4j 5.11+)
def get_entity_vector_index_query(dimension: int = 768) -> str:
    return f"""
CREATE VECTOR INDEX entity_embedding IF NOT EXISTS
FOR (n:Entity) ON (n.embedding)
OPTIONS {{indexConfig: {{
    `vector.dimensions`: {dimension},
    `vector.similarity_function`: 'cosine'
}}}}
"""

def get_relation_vector_index_query(dimension: int = 768) -> str:
    return f"""
CREATE VECTOR INDEX fact_embedding IF NOT EXISTS
FOR ()-[r:RELATION]-() ON (r.fact_embedding)
OPTIONS {{indexConfig: {{
    `vector.dimensions`: {dimension},
    `vector.similarity_function`: 'cosine'
}}}}
"""

def get_episode_vector_index_query(dimension: int = 768) -> str:
    return f"""
CREATE VECTOR INDEX episode_embedding IF NOT EXISTS
FOR (e:Episode) ON (e.embedding)
OPTIONS {{indexConfig: {{
    `vector.dimensions`: {dimension},
    `vector.similarity_function`: 'cosine'
}}}}
"""

# Fulltext indexes (for BM25 keyword search)
CREATE_ENTITY_FULLTEXT_INDEX = """
CREATE FULLTEXT INDEX entity_fulltext IF NOT EXISTS
FOR (n:Entity) ON EACH [n.name, n.summary]
"""

CREATE_FACT_FULLTEXT_INDEX = """
CREATE FULLTEXT INDEX fact_fulltext IF NOT EXISTS
FOR ()-[r:RELATION]-() ON EACH [r.fact, r.name]
"""

CREATE_EPISODE_FULLTEXT_INDEX = """
CREATE FULLTEXT INDEX episode_fulltext IF NOT EXISTS
FOR (e:Episode) ON EACH [e.data]
"""

CREATE_ENTITY_NAME_LOWER_INDEX = """
CREATE INDEX entity_name_lower IF NOT EXISTS
FOR (n:Entity) ON (n.name_lower)
"""

# All schema queries (as functions or constants)
def get_all_schema_queries(dimension: int = 768) -> list:
    return [
        CREATE_GRAPH_UUID_CONSTRAINT,
        CREATE_ENTITY_UUID_CONSTRAINT,
        CREATE_EPISODE_UUID_CONSTRAINT,
        CREATE_DOCUMENT_UUID_CONSTRAINT,
        CREATE_PAGE_UUID_CONSTRAINT,
        CREATE_ENTITY_GRAPH_ID_INDEX,
        CREATE_ENTITY_NAME_LOWER_INDEX,
        CREATE_DOC_GRAPH_ID_INDEX,
        CREATE_PAGE_GRAPH_ID_INDEX,
        CREATE_EPISODE_GRAPH_ID_INDEX,
        CREATE_EPISODE_SOURCE_INDEX,
        CREATE_EPISODE_CHUNK_INDEX,
        get_entity_vector_index_query(dimension),
        get_relation_vector_index_query(dimension),
        get_episode_vector_index_query(dimension),
        CREATE_ENTITY_FULLTEXT_INDEX,
        CREATE_FACT_FULLTEXT_INDEX,
        CREATE_EPISODE_FULLTEXT_INDEX,
    ]

# Keep this for backward compatibility if needed, but the storage class should call the function
ALL_SCHEMA_QUERIES = get_all_schema_queries(Config.EMBEDDING_DIMENSION)
