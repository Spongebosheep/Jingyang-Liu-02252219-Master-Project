"""Independent evaluation contracts for the PurrStone referral study.

The package is deliberately outside the Django ``interviews`` application.
Importing it must not change product state, contact OpenAI, or write results.
"""

from .schemas import (
    BASELINE_CONDITION,
    CONTRACT_SCHEMA_VERSION,
    MVP_CONDITION,
    ContractError,
)

__all__ = [
    "BASELINE_CONDITION",
    "CONTRACT_SCHEMA_VERSION",
    "MVP_CONDITION",
    "ContractError",
]
