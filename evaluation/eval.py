#!/usr/bin/env python3
"""
Prompt Evaluation Script

Evaluates an updated LLM prompt against a set of test cases defined in a JSON
evaluation file. Checks for required keywords per test case and computes a
weighted score. Exits with code 0 on pass, 1 on failure.

Usage:
    python eval.py --prompt-file <path> --eval-file <path> [--output-json <path>]
"""

import argparse
import json
import re
import sys
from pathlib import Path


def load_file(path: str) -> str:
    file_path = Path(path)
    if not file_path.exists():
        print(f"ERROR: File not found: {path}", file=sys.stderr)
        sys.exit(1)
    return file_path.read_text(encoding="utf-8")


def run_test_case(prompt_text: str, test_case: dict) -> dict:
    """Check that at least one required keyword appears in the prompt."""
    keywords = test_case.get("required_keywords", [])
    prompt_lower = prompt_text.lower()

    matched = [kw for kw in keywords if re.search(re.escape(kw.lower()), prompt_lower)]
    passed = len(matched) > 0

    return {
        "id": test_case["id"],
        "description": test_case.get("description", ""),
        "passed": passed,
        "matched_keywords": matched,
        "missing_keywords": [kw for kw in keywords if kw not in matched],
        "score_weight": test_case.get("score_weight", 0.0),
        "contribution": test_case.get("score_weight", 0.0) if passed else 0.0,
    }


def check_length(prompt_text: str, max_words: int) -> dict:
    word_count = len(prompt_text.split())
    passed = word_count <= max_words
    return {
        "id": "test_length",
        "description": f"Prompt length must not exceed {max_words} words",
        "passed": passed,
        "word_count": word_count,
        "max_words": max_words,
    }


def evaluate(prompt_file: str, eval_file: str) -> dict:
    prompt_text = load_file(prompt_file)
    eval_config = json.loads(load_file(eval_file))

    test_cases = eval_config.get("test_cases", [])
    criteria = eval_config.get("evaluation_criteria", {})
    threshold = criteria.get("threshold", 0.70)
    max_words = criteria.get("max_prompt_length_words", None)

    results = []
    total_score = 0.0
    total_weight = 0.0

    for tc in test_cases:
        result = run_test_case(prompt_text, tc)
        results.append(result)
        total_score += result["contribution"]
        total_weight += result["score_weight"]

    # Normalise to 0-1 if weights don't sum to 1
    final_score = (total_score / total_weight) if total_weight > 0 else 0.0

    length_result = None
    if max_words:
        length_result = check_length(prompt_text, max_words)

    passed_overall = final_score >= threshold and (
        length_result is None or length_result["passed"]
    )

    report = {
        "prompt_file": str(Path(prompt_file).resolve()),
        "eval_file": str(Path(eval_file).resolve()),
        "target_prompt": eval_config.get("target_prompt", "unknown"),
        "score": round(final_score, 4),
        "threshold": threshold,
        "passed": passed_overall,
        "test_results": results,
        "length_check": length_result,
    }

    return report


def print_report(report: dict) -> None:
    status = "PASSED" if report["passed"] else "FAILED"
    bar = "=" * 60
    print(f"\n{bar}")
    print(f"  Prompt Evaluation Report — {status}")
    print(bar)
    print(f"  Target Prompt : {report['target_prompt']}")
    print(f"  Prompt File   : {Path(report['prompt_file']).name}")
    print(f"  Score         : {report['score']:.2%}  (threshold: {report['threshold']:.0%})")
    print(f"{bar}\n")

    print("  Test Case Results:")
    for tc in report["test_results"]:
        icon = "✓" if tc["passed"] else "✗"
        print(f"  [{icon}] {tc['id']} (weight={tc['score_weight']:.2f})")
        print(f"      {tc['description']}")
        if tc["matched_keywords"]:
            print(f"      Matched : {', '.join(tc['matched_keywords'])}")
        if tc["missing_keywords"]:
            print(f"      Missing : {', '.join(tc['missing_keywords'])}")

    if report.get("length_check"):
        lc = report["length_check"]
        icon = "✓" if lc["passed"] else "✗"
        print(f"\n  [{icon}] Length check — {lc['word_count']} / {lc['max_words']} words")

    print(f"\n{'=' * 60}")
    print(f"  Overall: {status}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate an LLM prompt against test cases.")
    parser.add_argument("--prompt-file", required=True, help="Path to the prompt Markdown file")
    parser.add_argument("--eval-file", required=True, help="Path to the evaluation JSON file")
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional path to write the full JSON report",
    )
    args = parser.parse_args()

    report = evaluate(args.prompt_file, args.eval_file)
    print_report(report)

    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"  JSON report written to: {out_path}\n")

    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
