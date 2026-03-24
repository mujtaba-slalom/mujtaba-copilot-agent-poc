#!/usr/bin/env python3
"""
Prompt Enrichment Script — Real-World Data Validation

Fetches sample texts from a public HuggingFace dataset matching the prompt's
domain. Runs a keyword-coverage heuristic: for each sample, checks whether the
text shares vocabulary with the prompt's own instructions (treating the prompt
as a domain description). Computes a per-sample coverage ratio and a domain
drift score that flags when real-world data patterns diverge from what the
prompt was written to handle.

Injects or updates a structured HTML comment block at the very top of the
prompt file. This comment is visible in the GitHub PR diff and Markdown preview:

    <!-- [REAL-WORLD VALIDATION]
      source         : HuggingFace / rotten_tomatoes
      samples        : 15
      coverage       : 12/15 (80.0%)
      drift_score    : 0.14  (0.00 = perfect alignment, 1.00 = total mismatch)
      top_tokens     : good, bad, great, terrible, love
      low_coverage   : 3 samples had <20% token overlap with prompt vocabulary
      validated      : 2026-03-23T14:05:00Z
    -->

Usage:
    python3 scripts/enrich_prompt.py --prompt-file prompts/sentiment_analysis.md
    python3 scripts/enrich_prompt.py --prompt-file prompts/customer_support.md --samples 20
"""

import argparse
import json
import math
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Dataset registry
# Maps prompt stem → (dataset_id, config, split, text_field)
# All datasets are public on HuggingFace and require no authentication.
# ---------------------------------------------------------------------------
DATASET_MAP: dict[str, tuple[str, str, str, str]] = {
    "sentiment_analysis": (
        "rotten_tomatoes", "default", "test", "text"
    ),
    "summarization": (
        "xsum", "default", "test", "document"
    ),
    "customer_support": (
        "banking77", "default", "test", "text"
    ),
    "code_review": (
        "code_x_glue_ct_code_to_text", "python", "test", "code"
    ),
    "translation": (
        "Helsinki-NLP/opus-100", "en-fr", "test", "translation"
    ),
}

# Fallback for unknown prompt stems
FALLBACK_DATASET = ("rotten_tomatoes", "default", "test", "text")

HF_ROWS_URL = (
    "https://datasets-server.huggingface.co/rows"
    "?dataset={dataset}&config={config}&split={split}&offset=0&length={length}"
)

DEFAULT_SAMPLE_COUNT = 15
LOW_COVERAGE_THRESHOLD = 0.20   # samples below this per-sample overlap are flagged

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "is", "it", "this", "that", "are", "was", "be", "as",
    "by", "not", "from", "we", "you", "i", "do", "have", "will", "can",
    "should", "if", "its", "they", "their", "when", "which", "who", "how",
    "all", "any", "each", "more", "also", "then", "than", "so", "no",
}

COMMENT_RE = re.compile(r"<!--\s*\[REAL-WORLD VALIDATION\].*?-->", re.DOTALL)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_hf_samples(
    dataset: str, config: str, split: str, text_field: str, n: int
) -> list[str]:
    """
    Fetch n sample texts from the HuggingFace datasets server (no auth required).
    Returns a list of plain strings, one per sample row.
    """
    url = HF_ROWS_URL.format(
        dataset=urllib.parse.quote(dataset, safe="/-"),
        config=urllib.parse.quote(config, safe="/-"),
        split=split,
        length=n,
    )
    req = urllib.request.Request(
        url, headers={"User-Agent": "prompt-enricher/1.0"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # nosec — URL is constructed from a hardcoded template
        data = json.loads(resp.read().decode("utf-8"))

    texts: list[str] = []
    for row in data.get("rows", []):
        value = row.get("row", {}).get(text_field, "")
        if isinstance(value, dict):
            # translation pairs are dicts like {"en": "...", "fr": "..."}
            value = " ".join(str(v) for v in value.values())
        text = str(value).strip()
        if text:
            texts.append(text[:600])   # cap per-sample length

    return texts


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def tokenize(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in STOPWORDS]


def per_sample_overlap(sample: str, prompt_vocab: set[str]) -> float:
    """Jaccard-style overlap of sample tokens against the prompt vocabulary."""
    tokens = set(tokenize(sample))
    if not tokens:
        return 0.0
    return len(tokens & prompt_vocab) / len(tokens)


def compute_drift_score(overlap_ratios: list[float]) -> float:
    """
    Drift score: mean squared deviation of per-sample overlaps from the
    group mean. Range 0.0 (uniform, no drift) to ~1.0 (high variance).
    Uses population std-dev normalised by the mean overlap so datasets with
    naturally low overlap don't inflate the score.
    """
    if not overlap_ratios:
        return 0.0
    mean = sum(overlap_ratios) / len(overlap_ratios)
    variance = sum((x - mean) ** 2 for x in overlap_ratios) / len(overlap_ratios)
    std = math.sqrt(variance)
    # Normalise: divide std by (mean + ε) so that high-overlap datasets with
    # low variance score near 0, while volatile datasets score higher.
    return round(std / (mean + 1e-6), 4)


def analyse(samples: list[str], prompt_text: str) -> dict:
    prompt_vocab = set(tokenize(prompt_text))
    overlap_counter: Counter = Counter()
    overlap_ratios: list[float] = []
    covered = 0
    low_coverage_count = 0

    for text in samples:
        ratio = per_sample_overlap(text, prompt_vocab)
        overlap_ratios.append(ratio)
        if ratio > 0:
            covered += 1
            overlap_counter.update(set(tokenize(text)) & prompt_vocab)
        if ratio < LOW_COVERAGE_THRESHOLD:
            low_coverage_count += 1

    top_tokens = [t for t, _ in overlap_counter.most_common(7)]
    drift = compute_drift_score(overlap_ratios)

    return {
        "samples": len(samples),
        "covered": covered,
        "coverage_ratio": covered / len(samples) if samples else 0.0,
        "drift_score": drift,
        "top_tokens": top_tokens,
        "low_coverage_count": low_coverage_count,
    }


# ---------------------------------------------------------------------------
# Comment injection
# ---------------------------------------------------------------------------

def build_comment(dataset_id: str, stats: dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    n = stats["samples"]
    cov = stats["covered"]
    pct = stats["coverage_ratio"]
    drift = stats["drift_score"]
    tokens_str = ", ".join(stats["top_tokens"]) if stats["top_tokens"] else "n/a"
    low = stats["low_coverage_count"]

    drift_note = (
        "low — prompt vocabulary aligns well with real-world data" if drift < 0.30
        else "medium — some variation; consider broadening prompt coverage"
        if drift < 0.60
        else "high — prompt vocabulary diverges significantly from sampled data"
    )

    return (
        f"<!-- [REAL-WORLD VALIDATION]\n"
        f"  source         : HuggingFace / {dataset_id}\n"
        f"  samples        : {n}\n"
        f"  coverage       : {cov}/{n} ({pct:.1%})\n"
        f"  drift_score    : {drift:.4f}  ({drift_note})\n"
        f"  top_tokens     : {tokens_str}\n"
        f"  low_coverage   : {low} sample(s) had <{LOW_COVERAGE_THRESHOLD:.0%} token overlap\n"
        f"  validated      : {ts}\n"
        f"-->"
    )


def inject_comment(prompt_path: Path, comment: str) -> None:
    content = prompt_path.read_text(encoding="utf-8")
    if COMMENT_RE.search(content):
        updated = COMMENT_RE.sub(comment, content, count=1)
    else:
        updated = comment + "\n\n" + content
    prompt_path.write_text(updated, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enrich a prompt file with real-world data validation metadata."
    )
    parser.add_argument(
        "--prompt-file", required=True,
        help="Path to the prompt Markdown file to enrich",
    )
    parser.add_argument(
        "--samples", type=int, default=DEFAULT_SAMPLE_COUNT,
        help=f"Number of samples to fetch (default: {DEFAULT_SAMPLE_COUNT})",
    )
    args = parser.parse_args()

    prompt_path = Path(args.prompt_file)
    if not prompt_path.exists():
        print(f"ERROR: Prompt file not found: {args.prompt_file}", file=sys.stderr)
        sys.exit(1)

    stem = prompt_path.stem
    dataset_id, config, split, text_field = DATASET_MAP.get(stem, FALLBACK_DATASET)

    print(f"\n[Enrichment] Prompt   : {prompt_path.name}")
    print(f"[Enrichment] Dataset  : {dataset_id}  (config={config}, split={split})")
    print(f"[Enrichment] Fetching {args.samples} samples from HuggingFace datasets server...")

    try:
        samples = fetch_hf_samples(dataset_id, config, split, text_field, args.samples)
    except Exception as e:
        # Enrichment is non-fatal — network errors must not block the pipeline
        print(f"[Enrichment] WARNING: Could not fetch data: {e}", file=sys.stderr)
        print("[Enrichment] Skipping enrichment — prompt file unchanged.")
        sys.exit(0)

    if not samples:
        print("[Enrichment] WARNING: No samples returned — skipping enrichment.")
        sys.exit(0)

    print(f"[Enrichment] Fetched  : {len(samples)} samples")

    prompt_text = prompt_path.read_text(encoding="utf-8")
    stats = analyse(samples, prompt_text)

    print(f"[Enrichment] Coverage : {stats['covered']}/{stats['samples']} ({stats['coverage_ratio']:.1%})")
    print(f"[Enrichment] Drift    : {stats['drift_score']:.4f}")
    print(f"[Enrichment] Top tokens: {', '.join(stats['top_tokens']) or 'none'}")
    if stats["low_coverage_count"]:
        print(
            f"[Enrichment] Low-coverage samples: {stats['low_coverage_count']} "
            f"(<{LOW_COVERAGE_THRESHOLD:.0%} overlap)"
        )

    comment = build_comment(dataset_id, stats)
    inject_comment(prompt_path, comment)
    print(f"[Enrichment] Validation comment written to {prompt_path.name}")


if __name__ == "__main__":
    main()
