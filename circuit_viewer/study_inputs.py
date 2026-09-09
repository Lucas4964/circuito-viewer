"""Versão das entradas de um estudo, incluindo configurações e bibliotecas."""
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path


def _content(value):
    if is_dataclass(value):
        return {field.name: _content(getattr(value, field.name)) for field in fields(value) if field.init}
    if isinstance(value, Mapping):
        return {str(key): _content(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_content(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "tolist"):
        return value.tolist()
    return value


@dataclass(frozen=True, slots=True)
class StudyInputRevision:
    project_id: str
    revision: int
    configuration_digest: str

    @classmethod
    def capture(cls, project, **inputs):
        payload = json.dumps(_content(inputs), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return cls(project.project_id, project.revision, sha256(payload.encode("utf-8")).hexdigest())
