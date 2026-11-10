from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from prefect.client.orchestration import get_client
from prefect.client.schemas.objects import FlowRun
from prefect.states import StateType

from reconciliation.algorithms import DEFAULT_ALGORITHM, AlgorithmName

app = FastAPI(title="Reconciliation API")

DEPLOYMENT_NAME = "reconcile-dataset/default"


class ReconcileRequest(BaseModel):
    entities: list[dict]


class JobStatus(BaseModel):
    job_id: str
    status: str
    algorithm: str


class JobResult(BaseModel):
    job_id: str
    status: str
    algorithm: str
    result: list[dict] | None = None
    error: str | None = None


def _algorithm(flow_run: FlowRun) -> str:
    # Runs submitted before the parameter existed used the default.
    return flow_run.parameters.get("algorithm", DEFAULT_ALGORITHM.value)


@app.post("/reconcile", status_code=202, response_model=JobStatus)
async def submit_reconciliation(
    request: ReconcileRequest,
    algorithm: Annotated[
        AlgorithmName, Query(description="Reconciliation approach to run")
    ] = DEFAULT_ALGORITHM,
) -> JobStatus:
    async with get_client() as client:
        deployment = await client.read_deployment_by_name(DEPLOYMENT_NAME)
        flow_run = await client.create_flow_run_from_deployment(
            deployment.id,
            parameters={"entities": request.entities, "algorithm": algorithm.value},
        )
    return JobStatus(
        job_id=str(flow_run.id),
        status=flow_run.state.type.value,
        algorithm=algorithm.value,
    )


@app.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job_status(job_id: str) -> JobStatus:
    async with get_client() as client:
        try:
            flow_run = await client.read_flow_run(UUID(job_id))
        except Exception:
            raise HTTPException(status_code=404, detail="Job not found")
    return JobStatus(
        job_id=job_id, status=flow_run.state.type.value, algorithm=_algorithm(flow_run)
    )


@app.get("/jobs/{job_id}/result", response_model=JobResult)
async def get_job_result(job_id: str) -> JobResult:
    async with get_client() as client:
        try:
            flow_run = await client.read_flow_run(UUID(job_id))
        except Exception:
            raise HTTPException(status_code=404, detail="Job not found")

    state = flow_run.state
    status = state.type.value
    algorithm = _algorithm(flow_run)

    if state.type not in (StateType.COMPLETED, StateType.FAILED, StateType.CRASHED):
        return JobResult(job_id=job_id, status=status, algorithm=algorithm)

    if state.type == StateType.COMPLETED:
        try:
            result = await state.result(raise_on_failure=False)
            return JobResult(
                job_id=job_id, status=status, algorithm=algorithm, result=result
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch result: {e}")

    return JobResult(
        job_id=job_id, status=status, algorithm=algorithm, error=state.message
    )
