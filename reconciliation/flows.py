from prefect import flow, task

from reconciliation.algorithms.wikifier import reconcile
from reconciliation.models import Entity


@task(log_prints=True)
def reconcile_entity_task(entity_data: dict) -> dict:
    try:
        entity = Entity.model_validate(entity_data)
        result = reconcile(entity)
        return {"uri": entity.uri, "matches": result.model_dump()["matches"]}
    except Exception as e:
        print(f"Failed to reconcile {entity_data.get('uri')}: {e}")
        return {"uri": entity_data.get("uri"), "matches": [], "error": str(e)}


@flow(name="reconcile-dataset", persist_result=True, log_prints=True)
def reconcile_dataset(entities: list[dict]) -> list[dict]:
    futures = reconcile_entity_task.map(entities)
    return [f.result() for f in futures]
