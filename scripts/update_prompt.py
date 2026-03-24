#!/usr/bin/env python3
"""
Prompt Updater Script

Identifies the correct prompt file in the prompts/ directory based on either:
  1. The `target_prompt` field in the evaluation JSON, or
  2. Keyword similarity between the input text and existing prompt filenames/content.

Then applies the `update_instruction` from the eval JSON to the prompt file.
If an OpenAI-compatible API key is set via OPENAI_API_KEY, it uses the LLM to
generate the updated prompt. Otherwise it appends a structured "## Updates" section.

Usage:
    python update_prompt.py \\
        --input-prompt "Make the customer support prompt more empathetic" \\
        --eval-file evaluation/sample_eval.json \\
        --prompts-dir prompts/

Outputs the path of the updated prompt file on stdout (last line).
Exits with code 0 on success, 1 on error.
"""

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        print(f"ERROR: Eval file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def list_prompts(prompts_dir: str) -> list[Path]:
    d = Path(prompts_dir)
    if not d.is_dir():
        print(f"ERROR: Prompts directory not found: {prompts_dir}", file=sys.stderr)
        sys.exit(1)
    return sorted(d.glob("*.md"))


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, stripping punctuation."""
    return re.findall(r"[a-z0-9]+", text.lower())


def tfidf_score(query_tokens: list[str], doc_tokens: list[str]) -> float:
    """Rough TF-IDF cosine similarity (no IDF corpus — uses term frequency only)."""
    if not query_tokens or not doc_tokens:
        return 0.0

    tf_doc: dict[str, float] = {}
    for t in doc_tokens:
        tf_doc[t] = tf_doc.get(t, 0) + 1
    doc_len = len(doc_tokens)
    for t in tf_doc:
        tf_doc[t] /= doc_len

    tf_query: dict[str, float] = {}
    for t in query_tokens:
        tf_query[t] = tf_query.get(t, 0) + 1
    q_len = len(query_tokens)
    for t in tf_query:
        tf_query[t] /= q_len

    common = set(tf_query) & set(tf_doc)
    dot = sum(tf_query[t] * tf_doc[t] for t in common)
    mag_q = math.sqrt(sum(v ** 2 for v in tf_query.values()))
    mag_d = math.sqrt(sum(v ** 2 for v in tf_doc.values()))
    if mag_q == 0 or mag_d == 0:
        return 0.0
    return dot / (mag_q * mag_d)


def find_best_prompt(target_hint: str, input_text: str, prompt_files: list[Path]) -> Path:
    """
    Return the prompt file that best matches the given hint or input text.

    Priority:
      1. Exact stem match with the target_hint (e.g. "customer_support")
      2. Partial stem match
      3. TF-IDF similarity against file content
    """
    hint_tokens = tokenize(target_hint) if target_hint else []
    input_tokens = tokenize(input_text) if input_text else []
    combined_tokens = hint_tokens + input_tokens

    # 1. Exact stem match
    for pf in prompt_files:
        if pf.stem == target_hint.replace(" ", "_").lower():
            return pf

    # 2. Partial stem match (hint words appear in stem)
    if hint_tokens:
        for pf in prompt_files:
            stem_tokens = tokenize(pf.stem)
            if any(h in stem_tokens for h in hint_tokens):
                return pf

    # 3. TF-IDF similarity against file content
    scores = []
    for pf in prompt_files:
        content = pf.read_text(encoding="utf-8")
        doc_tokens = tokenize(pf.stem + " " + content)
        score = tfidf_score(combined_tokens, doc_tokens)
        scores.append((score, pf))

    scores.sort(key=lambda x: x[0], reverse=True)
    if scores:
        print(f"  Best match: {scores[0][1].name} (score={scores[0][0]:.4f})")
        return scores[0][1]

    print("ERROR: Could not identify a matching prompt file.", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Update strategies
# ---------------------------------------------------------------------------

def update_via_llm(current_content: str, instruction: str) -> str:
    """Use an OpenAI-compatible API to rewrite the prompt."""
    try:
        import openai  # type: ignore
    except ImportError:
        print(
            "  openai package not installed. Falling back to text append strategy.",
            file=sys.stderr,
        )
        return None  # type: ignore

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None  # type: ignore

    client = openai.OpenAI(api_key=api_key)
    system_msg = (
        "You are an expert prompt engineer. "
        "Rewrite the provided LLM system prompt according to the update instruction. "
        "Preserve the existing structure and Markdown formatting. "
        "Return only the updated prompt text — no explanation."
    )
    user_msg = (
        f"## Current Prompt\n\n{current_content}\n\n"
        f"## Update Instruction\n\n{instruction}"
    )

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content.strip()


def update_via_append(current_content: str, instruction: str) -> str:
    """Append a structured '## Updates Applied' section to the prompt."""
    separator = "\n\n---\n"
    updates_section = (
        f"## Updates Applied\n\n"
        f"The following changes were applied based on the update instruction:\n\n"
        f"> {instruction}\n\n"
        f"**Action required**: Review the above instruction and manually integrate "
        f"the changes into the sections above, then remove this section.\n"
    )
    if "## Updates Applied" in current_content:
        # Replace existing updates section
        current_content = re.sub(
            r"\n\n---\n## Updates Applied.*$",
            "",
            current_content,
            flags=re.DOTALL,
        )
    return current_content + separator + updates_section


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Identify and update an LLM prompt file.")
    parser.add_argument(
        "--input-prompt",
        required=True,
        help="Natural language description of the update to apply",
    )
    parser.add_argument(
        "--eval-file",
        required=True,
        help="Path to the evaluation JSON file",
    )
    parser.add_argument(
        "--prompts-dir",
        default="prompts",
        help="Directory containing prompt Markdown files (default: prompts/)",
    )
    args = parser.parse_args()

    eval_config = load_json(args.eval_file)
    target_hint = eval_config.get("target_prompt", "")
    update_instruction = eval_config.get(
        "update_instruction", args.input_prompt
    )

    prompt_files = list_prompts(args.prompts_dir)
    if not prompt_files:
        print(f"ERROR: No .md files found in {args.prompts_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"\nSearching for best matching prompt in '{args.prompts_dir}/'...")
    target_file = find_best_prompt(target_hint, args.input_prompt, prompt_files)
    print(f"  Selected prompt: {target_file.name}")

    current_content = target_file.read_text(encoding="utf-8")
    print(f"\nApplying update instruction:")
    print(f"  \"{update_instruction[:120]}{'...' if len(update_instruction) > 120 else ''}\"")

    # Try LLM first, fall back to append strategy
    updated_content = update_via_llm(current_content, update_instruction)
    if updated_content:
        print("  Strategy: LLM rewrite (OpenAI API)")
    else:
        updated_content = update_via_append(current_content, update_instruction)
        print("  Strategy: Structured append (no API key configured)")

    target_file.write_text(updated_content, encoding="utf-8")
    print(f"\nSuccessfully updated: {target_file}")

    # Print updated file path as last line so callers can parse it
    print(str(target_file.resolve()))


if __name__ == "__main__":
    main()
