"""The wire identity of a frozen Studio source, independent of its store."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Locator(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    blob_hash: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")
    page: int | None = Field(default=None, ge=1)
    region: tuple[float, float, float, float] | None = None
    quote: str = Field(default="", max_length=16000)
    path: str = Field(default="", max_length=500)
    line: int | None = Field(default=None, ge=1)


class ObjectRef(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    owner: Literal["current_account"] = "current_account"
    scope: str = Field(max_length=200)
    kind: str = Field(min_length=1, max_length=60)
    id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1)
    locator: Locator = Field(default_factory=Locator)
