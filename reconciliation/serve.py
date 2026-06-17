"""
Registers the reconcile-dataset deployment with the Prefect server
and starts polling for flow runs to execute.

Run once (or restart if you change flows.py):
    uv run python -m reconciliation.serve
"""
from reconciliation.flows import reconcile_dataset

if __name__ == "__main__":
    reconcile_dataset.serve(name="default")
