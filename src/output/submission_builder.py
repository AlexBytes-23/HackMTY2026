from __future__ import annotations
import json
import tempfile
import os
import math
from pathlib import Path
from typing import Any

from src.output.models import (
    Submission,
    SubmissionFinding,
    LeadNotPursued,
    RunMetadata,
    serialize_official_submission
)

def build_submission(
    findings: list[SubmissionFinding],
    leads_not_pursued: list[LeadNotPursued],
    run_metadata: RunMetadata,
    seed: int
) -> Submission:
    """
    Constructs the official Submission object deterministically.

    Does not modify findings, leads, or metadata.
    Preserves input order.
    """

    # Official model RunMetadata defines mxn_cost and wall_clock_seconds as non-optional floats.
    # It cannot represent "unknown" natively. We refuse NaN/Inf values rather than silently converting to 0.
    if not math.isfinite(run_metadata.mxn_cost):
        raise ValueError("RunMetadata.mxn_cost must be a finite float. Unknown costs are not natively supported by the frozen model.")
    if not math.isfinite(run_metadata.wall_clock_seconds):
        raise ValueError("RunMetadata.wall_clock_seconds must be a finite float.")

    return Submission(
        seed=seed,
        findings=list(findings),
        leads_not_pursued=list(leads_not_pursued),
        run_metadata=run_metadata
    )

def write_submission_json(submission: Submission, output_path: str | Path) -> None:
    """
    Writes the official Submission object to JSON atomically and deterministically.
    """
    dest = Path(output_path)

    # 1. Extract official dict representation (excludes None, uses aliases natively)
    raw_dict = serialize_official_submission(submission)

    # 2. Serialize to strict JSON
    # allow_nan=False guarantees standards-compliant JSON (blocks NaN/Infinity strings)
    try:
        json_data = json.dumps(
            raw_dict,
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
            allow_nan=False
        )
    except ValueError as e:
        raise ValueError(f"JSON serialization failed (likely due to NaN/Infinity in data): {e}")

    # 3. Ensure destination directory exists
    dest.parent.mkdir(parents=True, exist_ok=True)

    # 4. Write atomically
    fd, temp_path_str = tempfile.mkstemp(dir=dest.parent, prefix=".submission_tmp_")
    temp_path = Path(temp_path_str)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json_data)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_path, dest)
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink()
        raise e
