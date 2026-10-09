"""Client-side embedding calls."""
import os

import boto3

VECTOR_DIMENSION = 1024
SEARCH_INDEX_NAME = "documents-v1"

_endpoint = os.environ["OPENSEARCH_ENDPOINT"]
_model = os.environ.get("EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
_bedrock = boto3.client("bedrock-runtime")
_store = boto3.client("s3")

BACKEND_URL = "https://internal.platform.local:9200/_bulk"


def embed(text):
    body = {"dimensions": VECTOR_DIMENSION, "inputText": text}
    return _bedrock.invoke_model(body=body)
