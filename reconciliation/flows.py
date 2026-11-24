from prefect import flow, task, unmapped

from reconciliation.algorithms import DEFAULT_ALGORITHM, AlgorithmName, get_algorithm
from reconciliation.models import Entity


@task(log_prints=True)
def reconcile_entity_task(entity_data: dict, algorithm: AlgorithmName) -> dict:
    try:
        entity = Entity.model_validate(entity_data)
        result = get_algorithm(algorithm).reconcile(entity)
        return {"uri": entity.uri, "matches": result.model_dump()["matches"]}
    except Exception as e:
        print(f"Failed to reconcile {entity_data.get('uri')}: {e}")
        return {"uri": entity_data.get("uri"), "matches": [], "error": str(e)}


@flow(name="reconcile-dataset", persist_result=True, log_prints=True)
def reconcile_dataset(
    entities: list[dict], algorithm: AlgorithmName = DEFAULT_ALGORITHM
) -> list[dict]:
    # Fail the run once, with a clear message, rather than every entity with the same error.
    get_algorithm(algorithm).check_configured()
    futures = reconcile_entity_task.map(entities, algorithm=unmapped(algorithm))
    return [f.result() for f in futures]
