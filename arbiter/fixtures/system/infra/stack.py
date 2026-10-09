"""Platform stack (CDK-style)."""

VECTOR_DIMENSION = 512
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
SEARCH_INDEX_NAME = "documents-v1"


def container_environment():
    return {
        "EMBEDDING_MODEL_ID": EMBEDDING_MODEL_ID,
        "SEARCH_INDEX_NAME": SEARCH_INDEX_NAME,
        "LEGACY_FEATURE_FLAG": "off",
    }


def task_role_policy():
    return {
        "Statement": [
            {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"], "Resource": "*"},
            {"Effect": "Allow", "Action": ["es:ESHttpPost"], "Resource": "*"},
            {"Effect": "Allow", "Action": ["dynamodb:Query"], "Resource": "*"},
        ]
    }
