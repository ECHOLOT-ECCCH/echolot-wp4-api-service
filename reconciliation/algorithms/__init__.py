"""Reconciliation algorithms, and the registry the APIs use to pick one by name.

Each algorithm is a module that exposes:

- ``reconcile(entity: Entity) -> ReconciliationResult``
- ``check_configured() -> None``, which raises ``AlgorithmNotConfigured`` when a key,
  endpoint or data file the algorithm needs is missing, so callers can fail before
  doing any work
- ``UPSTREAM_ERRORS``, the exception types that mean a remote service failed

Modules are imported on first use, so an algorithm's dependencies and configuration
only matter once it is selected.

llm_based and rule_based contain the draft implementations, kept as they were; the
package __init__ modules only convert between this API's models and the drafts'
inputs and outputs.
"""

import importlib
from enum import Enum
from typing import Protocol, cast

from reconciliation.models import Entity, ReconciliationResult


class AlgorithmName(str, Enum):
    WIKIFIER = "wikifier"
    LLM_BASED = "llm_based"
    RULE_BASED = "rule_based"


DEFAULT_ALGORITHM = AlgorithmName.WIKIFIER

_MODULES = {
    AlgorithmName.WIKIFIER: "reconciliation.algorithms.wikifier",
    AlgorithmName.LLM_BASED: "reconciliation.algorithms.llm_based",
    AlgorithmName.RULE_BASED: "reconciliation.algorithms.rule_based",
}


class AlgorithmNotConfigured(Exception):
    """A key, endpoint or data file the algorithm needs is not configured."""


class Algorithm(Protocol):
    UPSTREAM_ERRORS: tuple[type[Exception], ...]

    def check_configured(self) -> None: ...

    def reconcile(self, entity: Entity) -> ReconciliationResult: ...


def get_algorithm(name: str) -> Algorithm:
    """Return the algorithm registered under `name`; raises ValueError for an unknown name."""
    return cast(Algorithm, importlib.import_module(_MODULES[AlgorithmName(name)]))


def wikidata_entity_url(qid: str) -> str:
    return f"http://www.wikidata.org/entity/{qid}"
