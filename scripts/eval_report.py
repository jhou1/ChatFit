"""Build a release scorecard from versioned Agent evaluation results."""

from __future__ import annotations

import argparse
from pathlib import Path

from evaluation.report import ExperimentReport


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, help="Experiment result JSON file")
    parser.add_argument(
        "--markdown",
        type=Path,
        help="Optional Markdown report output path",
    )
    args = parser.parse_args()

    report = ExperimentReport.model_validate_json(
        args.results.read_text(encoding="utf-8")
    )
    markdown = report.to_markdown()
    if args.markdown:
        args.markdown.write_text(markdown, encoding="utf-8")
    else:
        print(markdown, end="")

    gate = report.release_gate()
    return 0 if gate.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
