"""File routes, every one behind the auth dependency (ARB-083)."""
from fastapi import Depends, FastAPI

app = FastAPI()


def current_user() -> int:
    return 1


@app.get("/files")
def list_files(user_id: int = Depends(current_user)) -> list:
    return []


@app.delete("/files/{name}")
def delete_file(name: str, user_id: int = Depends(current_user)) -> None:
    return None
