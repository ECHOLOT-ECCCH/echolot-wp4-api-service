"""
Submit a reconciliation job, poll until done, save result to JSON.

Usage:
    python run_job.py [input.json] [output.json]

Defaults:
    input  → examples/in.json
    output → out.json
"""

import json
import sys
import time
from pathlib import Path

import httpx

API_BASE = "http://localhost:8000"
POLL_INTERVAL = 3  # seconds between status checks
TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CRASHED"}


def main(input_path: Path, output_path: Path) -> None:
    payload = json.loads(input_path.read_text())

    print(f"Submitting {len(payload['entities'])} entit(ies) from {input_path} ...")
    response = httpx.post(f"{API_BASE}/reconcile", json=payload, timeout=10)
    response.raise_for_status()

    job = response.json()
    job_id = job["job_id"]
    print(f"Job submitted: {job_id} (status: {job['status']})")
    print(f"Track in Prefect UI: http://localhost:4200")

    while True:
        time.sleep(POLL_INTERVAL)
        status_resp = httpx.get(f"{API_BASE}/jobs/{job_id}", timeout=10)
        status_resp.raise_for_status()
        status = status_resp.json()["status"]
        print(f"  status: {status}")

        if status in TERMINAL_STATUSES:
            break

    if status != "COMPLETED":
        print(f"Job ended with status {status}, no result to save.")
        sys.exit(1)

    result_resp = httpx.get(f"{API_BASE}/jobs/{job_id}/result", timeout=10)
    result_resp.raise_for_status()
    result = result_resp.json()

    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"Result saved to {output_path}")


if __name__ == "__main__":
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("examples/in.json")
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("out.json")
    main(input_path, output_path)
