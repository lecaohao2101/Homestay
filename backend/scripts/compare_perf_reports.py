import argparse
import json
from typing import Any


def _read_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _pct_change(*, baseline: float, candidate: float) -> float:
    if baseline == 0:
        return 0.0 if candidate == 0 else 100.0
    return ((candidate - baseline) / baseline) * 100


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare baseline and candidate performance reports")
    parser.add_argument("--baseline", required=True, help="Baseline JSON report path")
    parser.add_argument("--candidate", required=True, help="Candidate JSON report path")
    parser.add_argument(
        "--max-regression-pct",
        type=float,
        default=20.0,
        help="Fail threshold when latency metric regresses more than this percent",
    )
    args = parser.parse_args()

    baseline = _read_json(args.baseline)
    candidate = _read_json(args.candidate)

    metrics_to_check = ["avg_ms", "p95_ms", "p99_ms"]
    rows: list[dict[str, Any]] = []
    failed = False

    baseline_summary = baseline.get("summary", {})
    candidate_summary = candidate.get("summary", {})

    for metric in metrics_to_check:
        base_val = float(baseline_summary.get(metric, 0))
        cand_val = float(candidate_summary.get(metric, 0))
        delta_pct = _pct_change(baseline=base_val, candidate=cand_val)
        is_regressed = delta_pct > args.max_regression_pct
        failed = failed or is_regressed
        rows.append(
            {
                "metric": metric,
                "baseline": base_val,
                "candidate": cand_val,
                "delta_pct": round(delta_pct, 3),
                "regressed": is_regressed,
            }
        )

    output = {
        "baseline_file": args.baseline,
        "candidate_file": args.candidate,
        "threshold_pct": args.max_regression_pct,
        "result": "failed" if failed else "passed",
        "comparisons": rows,
    }
    print(json.dumps(output, ensure_ascii=True, indent=2))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
