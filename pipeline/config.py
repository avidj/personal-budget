# SPDX-License-Identifier: Apache-2.0
"""Runtime settings (environment) and the source list (sources.yaml)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://pipeline:pipeline@localhost:5432/pipeline"
    actual_url: str = "http://localhost:5006"
    actual_password: str = ""
    actual_file: str = "Household"
    actual_encryption_password: str | None = None
    actual_data_dir: Path = Path("/data/actual-cache")
    pipeline_api_token: str = ""
    inbox_dir: Path = Path("./inbox")
    sources_file: Path = Path("./sources.yaml")
    migrations_dir: Path = Path(__file__).resolve().parent.parent / "migrations"
    batch_ttl_seconds: int = 3600
    log_level: str = "INFO"
    # categorisation assistant (local LLM)
    ollama_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "llama3.1:8b"
    categorization_batch: int = 25  # payees per LLM call
    categorization_examples: int = (
        60  # known payee->category pairs shown to the model (rules first)
    )


class SourceConfig(BaseModel):
    id: str
    connector: str  # 'manual_import' | 'fints' | ...
    actual_account: str  # account name in Actual
    account_ref: str  # short alias used in keys and logs; never an account number
    currency: str = "EUR"
    # manual_import
    format: str | None = None  # format profile id, e.g. 'postbank-csv'
    inbox_glob: str | None = None  # relative to inbox dir, e.g. 'postbank-giro/*.csv'
    format_overrides: dict[str, str] = Field(default_factory=dict)  # column-name overrides


class SourcesConfig(BaseModel):
    sources: list[SourceConfig]

    def get(self, source_id: str) -> SourceConfig:
        for s in self.sources:
            if s.id == source_id:
                return s
        raise KeyError(f"unknown source '{source_id}'")


def load_sources(path: Path) -> SourcesConfig:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return SourcesConfig.model_validate(data)
