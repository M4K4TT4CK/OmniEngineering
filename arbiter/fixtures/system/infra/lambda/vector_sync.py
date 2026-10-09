"""Indexes rows into the search cluster."""

VECTOR_DIMENSION = 512


def index_mapping():
    return {"properties": {"vector": {"type": "knn_vector", "dimension": VECTOR_DIMENSION}}}
