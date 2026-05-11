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

def get_topic_vector_index_query(dimension: int = 768) -> str:
    return f"""
CREATE VECTOR INDEX topic_embedding IF NOT EXISTS
FOR (t:Topic) ON (t.embedding)
OPTIONS {{indexConfig: {{
    `vector.dimensions`: {dimension},
    `vector.similarity_function`: 'cosine'
}}}}
"""

def get_clause_vector_index_query(dimension: int = 768) -> str:
    return f"""
CREATE VECTOR INDEX clause_embedding IF NOT EXISTS
FOR (c:Clause) ON (c.embedding)
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

CREATE_TOPIC_FULLTEXT_INDEX = """
CREATE FULLTEXT INDEX topic_fulltext IF NOT EXISTS
FOR (t:Topic) ON EACH [t.topic, t.name]
"""

CREATE_CLAUSE_FULLTEXT_INDEX = """
CREATE FULLTEXT INDEX clause_fulltext IF NOT EXISTS
FOR (c:Clause) ON EACH [c.name, c.summary]
"""

CREATE_ENTITY_NAME_LOWER_INDEX = """
CREATE INDEX entity_name_lower IF NOT EXISTS
FOR (n:Entity) ON (n.name_lower)
"""

# Topic indexes
CREATE_TOPIC_UUID_CONSTRAINT = """
CREATE CONSTRAINT topic_uuid IF NOT EXISTS
FOR (t:Topic) REQUIRE t.uuid IS UNIQUE
"""

CREATE_TOPIC_GRAPH_ID_INDEX = """
CREATE INDEX topic_graph_id IF NOT EXISTS
FOR (t:Topic) ON (t.graph_id)
"""

CREATE_TOPIC_CLAUSE_ID_INDEX = """
CREATE INDEX topic_clause_id IF NOT EXISTS
FOR (t:Topic) ON (t.clause_id)
"""

# Clause indexes
CREATE_CLAUSE_UUID_CONSTRAINT = """
CREATE CONSTRAINT clause_uuid IF NOT EXISTS
FOR (c:Clause) REQUIRE c.uuid IS UNIQUE
"""

CREATE_CLAUSE_GRAPH_ID_INDEX = """
CREATE INDEX clause_graph_id IF NOT EXISTS
FOR (c:Clause) ON (c.graph_id)
"""

CREATE_CLAUSE_CLAUSE_ID_INDEX = """
CREATE INDEX clause_clause_id IF NOT EXISTS
FOR (c:Clause) ON (c.clause_id)
"""

# Image indexes
CREATE_IMAGE_UUID_CONSTRAINT = """
CREATE CONSTRAINT image_uuid IF NOT EXISTS
FOR (i:Image) REQUIRE i.uuid IS UNIQUE
"""

CREATE_IMAGE_GRAPH_ID_INDEX = """
CREATE INDEX image_graph_id IF NOT EXISTS
FOR (i:Image) ON (i.graph_id)
"""

# Table indexes
CREATE_TABLE_UUID_CONSTRAINT = """
CREATE CONSTRAINT table_uuid IF NOT EXISTS
FOR (t:Table) REQUIRE t.uuid IS UNIQUE
"""

CREATE_TABLE_GRAPH_ID_INDEX = """
CREATE INDEX table_graph_id IF NOT EXISTS
FOR (t:Table) ON (t.graph_id)
"""

# All schema queries (as functions or constants)
def get_all_schema_queries(dimension: int = 768) -> list:
    return [
        CREATE_GRAPH_UUID_CONSTRAINT,
        CREATE_ENTITY_UUID_CONSTRAINT,
        CREATE_EPISODE_UUID_CONSTRAINT,
        CREATE_DOCUMENT_UUID_CONSTRAINT,
        CREATE_PAGE_UUID_CONSTRAINT,
        CREATE_TOPIC_UUID_CONSTRAINT,
        CREATE_CLAUSE_UUID_CONSTRAINT,
        CREATE_ENTITY_GRAPH_ID_INDEX,
        CREATE_ENTITY_NAME_LOWER_INDEX,
        CREATE_DOC_GRAPH_ID_INDEX,
        CREATE_PAGE_GRAPH_ID_INDEX,
        CREATE_EPISODE_GRAPH_ID_INDEX,
        CREATE_EPISODE_SOURCE_INDEX,
        CREATE_EPISODE_CHUNK_INDEX,
        CREATE_TOPIC_GRAPH_ID_INDEX,
        CREATE_TOPIC_CLAUSE_ID_INDEX,
        CREATE_CLAUSE_GRAPH_ID_INDEX,
        CREATE_CLAUSE_CLAUSE_ID_INDEX,
        CREATE_IMAGE_UUID_CONSTRAINT,
        CREATE_IMAGE_GRAPH_ID_INDEX,
        CREATE_TABLE_UUID_CONSTRAINT,
        CREATE_TABLE_GRAPH_ID_INDEX,
        get_entity_vector_index_query(dimension),
        get_relation_vector_index_query(dimension),
        get_episode_vector_index_query(dimension),
        get_topic_vector_index_query(dimension),
        get_clause_vector_index_query(dimension),
        CREATE_ENTITY_FULLTEXT_INDEX,
        CREATE_FACT_FULLTEXT_INDEX,
        CREATE_EPISODE_FULLTEXT_INDEX,
        CREATE_TOPIC_FULLTEXT_INDEX,
        CREATE_CLAUSE_FULLTEXT_INDEX,
    ]

# Keep this for backward compatibility if needed, but the storage class should call the function
ALL_SCHEMA_QUERIES = get_all_schema_queries(Config.EMBEDDING_DIMENSION)
