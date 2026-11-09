from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("moviegraph-local")
    neo4j_database: str = "neo4j"
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = "qwen/qwen3.8-27b"
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_revision: str = "main"
    query_timeout: float = Field(default=5, gt=0)
    context_char_budget: int = Field(default=24000, ge=2000)
    synthesis_enabled: bool = True
    llm_input_usd_per_million: float | None = None
    llm_output_usd_per_million: float | None = None

    @model_validator(mode="before")
    @classmethod
    def empty_prices(cls, values):
        for key in ("llm_input_usd_per_million", "llm_output_usd_per_million"):
            if values.get(key) == "":
                values[key] = None
        return values
