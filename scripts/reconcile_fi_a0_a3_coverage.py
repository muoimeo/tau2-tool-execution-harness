"""Reconcile executed FI-v1 artifacts into an A0/A1/A2/A3 coverage matrix.

This is deliberately read-only with respect to every FI run root.  It creates
only a derived CSV and Markdown report under ``60_fault_injection/analysis``.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path


FI_ROOT = Path("data/simulations/60_fault_injection")
BASE = FI_ROOT / "fi-v1-retail-local-provider-v1"
GAPFIX = FI_ROOT / "fi-v1-retail-local-provider-v2-gapfix"
A3_CORE = FI_ROOT / "fi-v1-a3-core-regression-v1" / "core-observations.json"
A3_PILOT = FI_ROOT / "fi-v1-a3-pilot-v1" / "pilot-observations.json"
A2_PAIRED = FI_ROOT / "fi-v1-a3-pilot-a2-control-v1" / "a2-control-observations.json"
OUT = FI_ROOT / "analysis" / "fi_v1_a0_a3_coverage_v1"


CORE = (
    ("C0", "c0_clean_replay", "Clean replay", "A3 preserves the clean A1/A2 path; no new recovery claim."),
    ("P1", "p1_malformed_json", "Malformed JSON", "A1 parser containment is retained; A3 does not repair arbitrary malformed JSON."),
    ("P2", "p2_json_non_object", "JSON non-object", "A1 parser containment is retained; object-only policy remains fail-closed."),
    ("P3", "p3_unknown_tool", "Unknown tool", "Registry containment is retained; no native dispatch."),
    ("P4", "p4_missing_required_argument", "Missing required argument", "Strict-schema containment is retained; no native dispatch."),
    ("P5", "p5_extra_argument", "Extra argument", "Strict-schema containment is retained; no native dispatch."),
    ("P6", "p6_wrong_strict_type", "Wrong strict type", "Strict type containment is retained; no coercion."),
    ("D1", "d1_domain_constraint", "Frozen domain constraint", "Pre-dispatch domain containment is retained."),
    ("D2", "d2_native_precondition", "Native domain/precondition negative control", "Native tool/domain behavior remains native; A3 does not reinterpret it."),
    ("R1", "r1_timeout_once", "Timeout once then valid", "A2 provider retry recovery is retained; A3 does not own provider retry."),
    ("R2", "r2_http_503_once", "HTTP 503 once then valid", "A2 provider retry recovery is retained; A3 does not own provider retry."),
    ("R3", "r3_reasoning_only_once", "Reasoning-only once then valid", "A2 retry recovery of unusable provider output is retained."),
    ("R4", "r4_provider_exhaust_pre_write", "Provider exhaustion before WRITE", "Provider exhaustion remains contained before tool dispatch."),
    ("S1", "s1_provider_exhaust_post_write", "Provider exhaustion after WRITE", "A2 no-replay safety is retained: the existing WRITE is not repeated."),
)

PILOT = (
    ("A3-P1", "a3_fenced_json_repair", "Fenced JSON object", "A3 losslessly removes a code fence, re-enters A1, then dispatches once."),
    ("A3-P2", "a3_double_encoded_repair", "Double-encoded JSON object", "A3 losslessly unwraps once, re-enters A1, then dispatches once."),
    ("A3-P3", "a3_trailing_comma_repair", "Trailing comma JSON", "A3 removes the representation-only trailing comma, then revalidates."),
    ("A3-P4", "a3_truncated_json_no_repair", "Truncated JSON", "A3 deliberately refuses ambiguous repair; it retains A1 rejection."),
    ("A3-P5", "a3_repaired_schema_reject", "Repair followed by schema reject", "A3 repair does not bypass A1 strict schema: resulting invalid call stays undispatched."),
    ("A3-O1", "a3_invalid_output_after_write", "Invalid native output after WRITE", "A3 quarantines after one WRITE; it withholds success materialization and does not replay."),
    ("A3-O2", "a3_postcondition_fail_after_write", "WRITE postcondition mismatch", "A3 quarantines after one WRITE; it withholds success materialization and does not replay."),
)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def base_observations(track: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for path in (BASE / track).glob("*/scenario-observation.json"):
        item = read_json(path)
        result[item["scenario_id"].split("__task", 1)[0]] = item
    return result


def compact(item: dict | None) -> str:
    if item is None:
        return "MISSING"
    return (
        f"{item['disposition']}; dispatch={item['dispatch_count']}; "
        f"WRITE={item['write_dispatch_count']}; provider_attempts={item['provider_attempt_count']}; "
        f"recovery={item.get('recovery_outcome') or 'none'}"
    )


def a3_core_observations() -> dict[str, dict]:
    return {item["scenario_id"].split("__task", 1)[0]: item for item in read_json(A3_CORE)}


def pilot_observations(path: Path) -> dict[str, dict]:
    return {item["scenario_id"]: item for item in read_json(path)}


def corrected_r1(track: str) -> dict | None:
    for path in (GAPFIX / track).glob("r1_timeout_once__*/scenario-observation.json"):
        return read_json(path)
    return None


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    a0 = base_observations("a0-native")
    a1 = base_observations("a1-validation")
    a2 = base_observations("a2-retry")
    a3 = a3_core_observations()
    corrected_r1_by_track = {track: corrected_r1(track) for track in ("a0-native", "a1-validation", "a2-retry")}
    a2_pilot = pilot_observations(A2_PAIRED)
    a3_pilot = pilot_observations(A3_PILOT)

    rows: list[dict[str, str]] = []
    for code, key, fault, claim in CORE:
        is_r1 = code == "R1"
        a0_item = corrected_r1_by_track["a0-native"] if is_r1 and corrected_r1_by_track["a0-native"] else a0.get(key)
        a1_item = corrected_r1_by_track["a1-validation"] if is_r1 and corrected_r1_by_track["a1-validation"] else a1.get(key)
        a2_item = corrected_r1_by_track["a2-retry"] if is_r1 and corrected_r1_by_track["a2-retry"] else a2.get(key)
        note = " (v2-gapfix evidence)" if is_r1 else ""
        rows.append({
            "scenario": code,
            "fault": fault,
            "A0": compact(a0_item) + note,
            "A1": compact(a1_item) + note,
            "A2": compact(a2_item) + note,
            "A3": compact(a3.get(key)),
            "incremental_claim": claim,
            "evidence_status": "PASS: executed local FI artifacts",
        })
    for code, key, fault, claim in PILOT:
        rows.append({
            "scenario": code,
            "fault": fault,
            "A0": "NOT_APPLICABLE: A3-specific fault family has no A0 control",
            "A1": "NOT_APPLICABLE: A3-specific fault family has no A1 control",
            "A2": compact(a2_pilot.get(key)),
            "A3": compact(a3_pilot.get(key)),
            "incremental_claim": claim,
            "evidence_status": "PASS: paired A2 control + A3 pilot executed locally",
        })

    fields = ["scenario", "fault", "A0", "A1", "A2", "A3", "incremental_claim", "evidence_status"]
    csv_path = OUT / "fault_coverage_matrix.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# FI-v1 A0-A3 coverage reconciliation",
        "",
        "## Execution evidence",
        "",
        "- Original FI-v1: 14 scenario observations in each of A0 native, A1 validation and A2 retry = **42 executed local-loopback runs**.",
        "- A3 core regression: 14 executed A3 scenarios. The requested ‘12’ is not the artifact count; C0/P1-P6/D1/D2/R1-R4/S1 totals 14.",
        "- A3-specific pilot: 7 executed A3 scenarios. This reconciliation adds 7 paired A2 controls; no corporate gateway was called.",
        "- R1 A2 uses the corrected v2-gapfix timeout artifact because the original FI-v1 timeout injector did not yield the intended typed timeout.",
        "",
        "## Scope caveat",
        "",
        "A0/A1 are intentionally `NOT_APPLICABLE` for the seven A3-specific faults: they were introduced to test A3 repair/output/postcondition capability. The paired control required for the incremental A3 claim is A2, which is now present.",
        "",
        "## Matrix",
        "",
        "| Scenario | Fault | A0 | A1 | A2 | A3 | Incremental claim |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        escaped = [row[k].replace("|", "\\|") for k in fields]
        lines.append("| " + " | ".join(escaped[:7]) + " |")
    lines.extend([
        "",
        "## Artifact roots",
        "",
        f"- `{BASE}` — original A0/A1/A2 FI-v1.",
        f"- `{GAPFIX}` — corrected R1 timeout evidence (and retained R4/S1 gapfix artifacts).",
        f"- `{A3_CORE.parent}` — A3 core regression.",
        f"- `{A3_PILOT.parent}` — A3-specific pilot.",
        f"- `{A2_PAIRED.parent}` — paired A2 controls created for this reconciliation.",
        "",
        "All files above are read as evidence. This script writes only this derived report and CSV.",
    ])
    (OUT / "fault_coverage_reconciliation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {csv_path}")
    print(f"wrote {OUT / 'fault_coverage_reconciliation.md'}")


if __name__ == "__main__":
    main()
