"""CanopyLens test suite."""

from __future__ import annotations

import time

# Generous: the bound is there to catch a job that will never settle, not to
# assert how fast the machine running the tests happens to be.
JOB_TIMEOUT_SECONDS = 120.0


def await_job(client, job_id: str, timeout: float = JOB_TIMEOUT_SECONDS) -> dict:
    """Poll a job until it finishes, the way the browser does.

    Analyses run on a bounded pool, so a submission returns while the work is
    still in flight. Returns the final status body; fails the test if the job
    is still unfinished when the timeout expires, rather than hanging the suite.
    """
    deadline = time.monotonic() + timeout
    while True:
        status = client.get(f"/api/jobs/{job_id}").json()
        if status["status"] in ("completed", "failed"):
            return status
        assert time.monotonic() < deadline, f"job {job_id} stuck at {status['stage']!r}"
        time.sleep(0.05)
