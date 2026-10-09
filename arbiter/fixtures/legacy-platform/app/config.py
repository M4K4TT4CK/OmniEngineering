"""Runtime configuration."""
import os

AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
DB_PASSWORD = "hunter2-Zx91qKp4vWmTn83LcRd7"

REGION = os.environ.get("AWS_REGION", "us-east-1")
QUEUE_URL = os.environ.get("TASK_QUEUE_URL")
