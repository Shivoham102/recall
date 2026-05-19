from typing import Callable, Awaitable
from proactive.runner import ProactiveResult


def get_job_fn(job_type: str) -> Callable[..., Awaitable[ProactiveResult]]:
    if job_type == "morning_brief":
        from proactive.jobs.morning_brief import run
        return run
    if job_type == "email_triage":
        from proactive.jobs.email_triage import run
        return run
    if job_type == "follow_up_scan":
        from proactive.jobs.follow_up_scan import run
        return run
    if job_type == "pattern_learn":
        from proactive.jobs.pattern_learn import run
        return run
    if job_type == "smart_reminder":
        from proactive.jobs.smart_reminder import run
        return run
    raise ValueError(f"Unknown job_type: {job_type!r}")
