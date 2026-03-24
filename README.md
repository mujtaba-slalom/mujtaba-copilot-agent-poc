# Prompt Updater — GitHub Copilot Agent POC

A GitHub Copilot custom agent that automatically identifies, updates, and evaluates LLM system prompts, then opens a pull request with the changes.

---

## How It Works

```
User input (text + eval JSON)
         │
         ▼
┌─────────────────────────┐
│  Identify target prompt │  ← keyword / TF-IDF match against prompts/
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│   Update prompt file    │  ← LLM rewrite (if API key) or structured append
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│   Run evaluation script │  ← keyword scoring against eval JSON test cases
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│   Create Pull Request   │  ← git branch + gh pr create / GitHub Actions
└─────────────────────────┘
```

---

## Repository Structure

```
.github/
  agents/
    prompt-updater.agent.md      ← VS Code Copilot custom agent definition
  workflows/
    prompt-updater.yml           ← GitHub Actions workflow (triggered from issues)
  ISSUE_TEMPLATE/
    prompt-update.yml            ← Structured issue form for prompt update requests
prompts/                         ← LLM system prompts (5 samples)
  customer_support.md
  code_review.md
  summarization.md
  translation.md
  sentiment_analysis.md
evaluation/
  eval.py                        ← Evaluation script
  sample_eval.json               ← Sample evaluation configuration
scripts/
  update_prompt.py               ← Prompt identifier + updater script
```

---

## Usage

### Option 1 — VS Code Copilot Agent (local)

1. Open this repository in VS Code with GitHub Copilot enabled.
2. Open Copilot Chat and select the **Prompt Updater** agent from the agent picker (or type `@prompt-updater`).
3. Describe the update:
   ```
   Make the customer support prompt more empathetic and add an escalation path.
   Eval file: evaluation/sample_eval.json
   ```
4. The agent will:
   - Identify `prompts/customer_support.md` as the target
   - Apply the changes
   - Run `evaluation/eval.py`
   - Create a branch and open a pull request

### Option 2 — GitHub Issue (automated)

1. Open a new issue using the **Prompt Update Request** template.
2. Fill in the **Target Prompt**, **Update Instruction**, and optionally paste an **Evaluation Config JSON**.
3. Submit the issue — the `prompt-update` label is applied automatically.
4. The GitHub Actions workflow triggers, runs all steps, and:
   - Posts a comment on the issue with results and a link to the PR
   - Opens a pull request targeting `main`

---

## Evaluation Config JSON Schema

```json
{
  "target_prompt": "customer_support",
  "update_instruction": "Add escalation handling and callback option",
  "test_cases": [
    {
      "id": "test_escalation",
      "description": "Prompt includes escalation path",
      "required_keywords": ["escalat", "supervisor"],
      "score_weight": 0.5
    }
  ],
  "evaluation_criteria": {
    "threshold": 0.70,
    "max_prompt_length_words": 600
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `target_prompt` | Yes | Stem of the prompt file (e.g. `customer_support`) |
| `update_instruction` | Yes | What changes to apply |
| `test_cases[].required_keywords` | Yes | At least one must appear in the updated prompt |
| `test_cases[].score_weight` | Yes | Weights must sum to 1.0 |
| `evaluation_criteria.threshold` | Yes | Minimum score to pass (0.0–1.0) |

---

## Running Scripts Locally

```bash
# Update a prompt
python scripts/update_prompt.py \
  --input-prompt "Make the customer support prompt more empathetic" \
  --eval-file evaluation/sample_eval.json \
  --prompts-dir prompts/

# Evaluate a prompt
python evaluation/eval.py \
  --prompt-file prompts/customer_support.md \
  --eval-file evaluation/sample_eval.json \
  --output-json evaluation/last_eval_report.json
```

Set `OPENAI_API_KEY` to enable LLM-powered prompt rewriting in `update_prompt.py`. Without it, a structured update section is appended instead.

---

## Repository Secrets

| Secret | Purpose |
|--------|---------|
| `OPENAI_API_KEY` | (Optional) Enables LLM rewriting in the update script |
| `GITHUB_TOKEN` | Automatically provided by GitHub Actions; used for PR creation |

