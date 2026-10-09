import os

from .embeddings import embed

INDEX = os.environ.get("SEARCH_INDEX_NAME", "documents-v1")


def query(text):
    return {"index": INDEX, "vector": embed(text)}
