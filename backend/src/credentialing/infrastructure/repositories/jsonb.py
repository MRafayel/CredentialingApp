"""Conversion of domain values into ``jsonb`` (and ``json``) parameters, written once for every
repository."""

from collections.abc import Iterable, Mapping

from psycopg.types.json import Json, Jsonb
from pydantic import BaseModel


def model_jsonb(model: BaseModel) -> Jsonb:
    """A model as a JSON object, in the same shape pydantic reads back."""
    return Jsonb(model.model_dump(mode="json"))


def model_json(model: BaseModel) -> Json:
    """A model as a ``json`` (not ``jsonb``) object, for a column that keeps key order."""
    return Json(model.model_dump(mode="json"))


def models_jsonb(models: Iterable[BaseModel]) -> Jsonb:
    """Models as a JSON array of objects."""
    return Jsonb([model.model_dump(mode="json") for model in models])


def mapping_jsonb(mapping: Mapping[str, str]) -> Jsonb:
    """A string-to-string mapping as a JSON object."""
    return Jsonb(dict(mapping))
