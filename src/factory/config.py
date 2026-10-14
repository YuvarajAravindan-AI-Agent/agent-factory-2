"""models.yaml loader. A model change touches this file's input, never code."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator


class ModelConfig(BaseModel):
    tiers: dict[str, str]
    roles: dict[str, str]
    temperature: float = 0.0

    @model_validator(mode="after")
    def _check(self) -> ModelConfig:
        for role, tier in self.roles.items():
            if tier not in self.tiers:
                raise ValueError(f"role {role!r} uses unknown tier {tier!r}")
        if self.roles.get("reviewer") == self.roles.get("coder"):
            raise ValueError("reviewer must use a different tier from the coder")
        if not 0.0 <= self.temperature <= 0.2:
            raise ValueError("temperature must be between 0 and 0.2")
        return self

    def model_id(self, tier: str) -> str:
        return self.tiers[tier]


def load_model_config(path: Path | str = "models.yaml") -> ModelConfig:
    return ModelConfig.model_validate(yaml.safe_load(Path(path).read_text()))
