from dataclasses import dataclass


@dataclass
class Job:
    id: str
    payload: dict
