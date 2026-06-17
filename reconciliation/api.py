from uuid import UUID

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from prefect.client.orchestration import get_client
from prefect.states import StateType

app = FastAPI(title="Reconciliation API")

DEPLOYMENT_NAME = "reconcile-dataset/default"


class ReconcileRequest(BaseModel):
    entities: list[dict]


class JobStatus(BaseModel):
    job_id: str
    status: str


class JobResult(BaseModel):
    job_id: str
    status: str
    result: list[dict] | None = None
    error: str | None = None


@app.post("/reconcile", status_code=202, response_model=JobStatus)
async def submit_reconciliation(request: ReconcileRequest) -> JobStatus:
    async with get_client() as client:
        deployment = await client.read_deployment_by_name(DEPLOYMENT_NAME)
        flow_run = await client.create_flow_run_from_deployment(
            deployment.id,
            parameters={"entities": request.entities},
        )
    return JobStatus(job_id=str(flow_run.id), status=flow_run.state.type.value)


@app.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job_status(job_id: str) -> JobStatus:
    async with get_client() as client:
        try:
            flow_run = await client.read_flow_run(UUID(job_id))
        except Exception:
            raise HTTPException(status_code=404, detail="Job not found")
    return JobStatus(job_id=job_id, status=flow_run.state.type.value)


@app.get("/jobs/{job_id}/result", response_model=JobResult)
async def get_job_result(job_id: str) -> JobResult:
    async with get_client() as client:
        try:
            flow_run = await client.read_flow_run(UUID(job_id))
        except Exception:
            raise HTTPException(status_code=404, detail="Job not found")

    state = flow_run.state
    status = state.type.value

    if state.type not in (StateType.COMPLETED, StateType.FAILED, StateType.CRASHED):
        return JobResult(job_id=job_id, status=status)

    if state.type == StateType.COMPLETED:
        try:
            result = await state.result(raise_on_failure=False)
            return JobResult(job_id=job_id, status=status, result=result)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch result: {e}")

    return JobResult(job_id=job_id, status=status, error=state.message)
