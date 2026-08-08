# Quick Start Guide - Evaluation Framework

## 5-Minute Setup

### 1. Explore the Structure

```bash
cd backend/evaluation
ls -la
```

You'll see:
- `metrics.py` - All metric calculations
- `benchmark.py` - Test suite runner
- `transcripts/` - Test transcripts (JSON)
- `expected_outputs/` - Reference outputs (JSON)
- `README.md` - Full documentation
- `ARCHITECTURE.md` - Design details

### 2. Run Your First Evaluation

```bash
cd backend
python -m pytest evaluation/test_evaluation.py::example_basic_evaluation -v
```

Or use Python directly:

```python
from evaluation.benchmark import BenchmarkSuite
import json

# Initialize
suite = BenchmarkSuite(
    transcripts_dir="evaluation/transcripts",
    expected_outputs_dir="evaluation/expected_outputs",
    prompt_version="v1.0"
)

# Run (validation mode - no AI calls)
results = suite.run_all()

# Print report
suite.print_report(results)

# Export results
suite.export_results(results, "evaluation/results_v1.0.json")
```

### 3. Understand Your Scores

```
Quality Score: 84.5/100

Breakdown:
├─ Contract Compliance: 95% (structure + fields correct)
├─ Timestamp Accuracy: 87% (timestamps close to expected)
├─ Virality Scoring: 82% (scores match expected rankings)
└─ Grounding: 100% (no hallucinations detected)

✅ Status: Production Ready (score ≥ 80)
```

**What do the components mean?**

| Metric | Means | Good Range |
|--------|-------|------------|
| Contract Compliance | Output follows RELLS Engine spec | > 95% |
| Timestamp Accuracy | Time ranges match expected | > 85% |
| Virality Scoring | Scores align with reference | > 80% |
| Grounding | No made-up content | < 5% hallucinations |

---

## Common Tasks

### Task 1: Test a Single Case

```python
from evaluation.benchmark import BenchmarkSuite
import json

suite = BenchmarkSuite("evaluation/transcripts", "evaluation/expected_outputs")

# Load data
transcript = suite.load_transcript("example_001.json")
expected = suite.load_expected_output("example_001.json")

# Your LLM output (JSON string)
output = {
    "most_relevant_segments": [...],
    "summary": "...",
    "key_topics": [...]
}

# Evaluate
result = suite.evaluate_single(
    test_name="example_001",
    output_json_str=json.dumps(output),
    transcript_text=transcript["content"],
    expected_output=expected,
    execution_time=2.5  # seconds
)

# View results
print(f"Score: {result.overall_quality_score:.1f}/100")
print(f"Issues: {result.contract_metrics.issues_found}")
```

### Task 2: Compare Two Prompt Versions

```python
import json

# Load both versions' results
with open("evaluation/results_v1.0.json") as f:
    v1_data = json.load(f)
    
with open("evaluation/results_v1.1.json") as f:
    v2_data = json.load(f)

# Extract results (would need to reconstruct BenchmarkResult objects)
# For now, just compare scores manually:

v1_scores = [r["overall_quality_score"] for r in v1_data["results"]]
v2_scores = [r["overall_quality_score"] for r in v2_data["results"]]

v1_mean = sum(v1_scores) / len(v1_scores)
v2_mean = sum(v2_scores) / len(v2_scores)

print(f"v1.0 mean score: {v1_mean:.1f}")
print(f"v1.1 mean score: {v2_mean:.1f}")
print(f"Improvement: {v2_mean - v1_mean:+.1f} points")

# Check for regressions
for i, (s1, s2) in enumerate(zip(v1_scores, v2_scores)):
    if s2 < s1 - 5:
        print(f"⚠️  Regression on test {i}: {s1:.1f} → {s2:.1f}")
```

### Task 3: Test with Your AI Pipeline

```python
from evaluation.benchmark import BenchmarkSuite
from src.ai import get_most_relevant_parts_by_transcript
import json

def my_ai_executor(transcript_text: str) -> str:
    """Call your actual AI pipeline."""
    result = get_most_relevant_parts_by_transcript(transcript_text)
    return json.dumps(result)

# Run with AI execution
suite = BenchmarkSuite(
    "evaluation/transcripts",
    "evaluation/expected_outputs",
    prompt_version="v1.0"
)

results = suite.run_all(ai_executor=my_ai_executor)
suite.print_report(results)
suite.export_results(results, "evaluation/results_v1.0_real.json")
```

### Task 4: Add Your Own Test Case

**Step 1**: Create transcript (`evaluation/transcripts/my_test.json`)
```json
{
  "id": "my_test",
  "content": "Your transcript text here...",
  "duration_seconds": 600
}
```

**Step 2**: Create expected output (`evaluation/expected_outputs/my_test.json`)
```json
{
  "test_id": "my_test",
  "most_relevant_segments": [
    {
      "start_time": "00:30",
      "end_time": "01:00",
      "text": "Best clip from transcript",
      "relevance_score": 85,
      "reasoning": "Why this is a good clip",
      "virality": {
        "hook_score": 22,
        "engagement_score": 21,
        "value_score": 23,
        "shareability_score": 20,
        "total_score": 86,
        "hook_type": "statement",
        "virality_reasoning": "Why people would share this"
      },
      "hook_title": "Catchy 3-9 word title"
    }
  ],
  "summary": "One-line summary",
  "key_topics": ["topic1", "topic2"]
}
```

**Step 3**: Run evaluation
```python
suite = BenchmarkSuite("evaluation/transcripts", "evaluation/expected_outputs")
results = suite.run_all()  # Automatically includes your new test
suite.print_report(results)
```

---

## Workflow: Improving the Prompt

### Week 1: Establish Baseline

```bash
# Run validation on current prompt
python -c "
from backend.evaluation.benchmark import BenchmarkSuite
suite = BenchmarkSuite('backend/evaluation/transcripts', 'backend/evaluation/expected_outputs', 'v1.0')
results = suite.run_all()
suite.print_report(results)
suite.export_results(results, 'baseline.json')
"

# Save output: baseline score 78.5/100
```

### Week 2-3: Modify Prompt

```bash
# Edit the prompt to improve quality
nano backend/prompts/rells_engine.md

# Make changes focused on:
# - Better virality scoring rules
# - More precise timestamp guidance
# - Clearer grounding requirements
```

### Week 4: Test New Version

```bash
# Run evaluation with new prompt
python -c "
from backend.evaluation.benchmark import BenchmarkSuite
suite = BenchmarkSuite('backend/evaluation/transcripts', 'backend/evaluation/expected_outputs', 'v1.1')
results = suite.run_all()
suite.print_report(results)
suite.export_results(results, 'v1.1_results.json')
"

# Output: v1.1 score 82.3/100
# ✅ Improvement of +3.8 points - good to ship!
```

### Week 5: Deploy

```bash
# PromptManager automatically uses new prompt via mtime detection
# No backend restart needed
# New metrics in git commit message
```

---

## Report Interpretation

### Example Report Output

```
================================================================================
RELLS Engine Benchmark Report - v1.1
================================================================================

Overall Quality Metrics:
  Mean Score:           82.3/100  ← Your quality score
  Min Score:            76.5/100  ← Worst case
  Max Score:            88.1/100  ← Best case
  Tests Passed (≥80):   3/4       ← Production ready tests

Timestamp Accuracy:
  Mean Error:           1.45s     ← How far off timestamps are
  Within 2s:            85.0%     ← % of segments within 2s tolerance

Virality Scoring:
  Mean Absolute Error:  3.20/100  ← How far off virality scores are
  Mean Correlation:     0.892     ← Alignment with expected (0-1 scale)

Content Grounding:
  Hallucination Rate:   5.0%      ← % of made-up content (lower is better)
  Contract Compliance:  97.5%     ← % following output format

Performance:
  Mean Exec Time:       2.34s     ← Inference time per test
  Total Est. Cost:      $0.002450 ← Approximate API cost
```

### Score Meanings

```
90-100: Excellent       ⭐⭐⭐⭐⭐
80-89:  Good            ⭐⭐⭐⭐
70-79:  Acceptable      ⭐⭐⭐
60-69:  Needs Work      ⭐⭐
0-59:   Not Ready       ⭐

Recommendation:
├─ ≥ 80: Ship to production
├─ 70-79: Consider improvement
└─ < 70: Major revision needed
```

---

## Troubleshooting

### "No tests found"
```bash
# Check file structure
ls backend/evaluation/transcripts/
ls backend/evaluation/expected_outputs/

# Filenames must match (without extension)
# Example:
#   transcripts/test_001.json
#   expected_outputs/test_001.json
```

### "Invalid JSON in output"
```python
# Check output format
output_json = result.contract_metrics.issues_found
print(output_json)

# Common issues:
# - Missing quotes around field names
# - Trailing commas
# - Wrong data types
```

### "High hallucination rate"
```bash
# Check if segment text appears in transcript
# False positives if:
# - Paraphrased (algorithm uses exact string match)
# - Text appears in transcript but different context

# Review: result.grounding_metrics.grounding_violations
```

### "Timestamp error too high"
```bash
# Timestamp format must be MM:SS
# Examples: "00:30", "01:15", "10:45"
# Not: "0:30" or "1m15s"

# Check expected_outputs/ for correct format
```

---

## Next Steps

1. **Add more test cases**: Create 5-10 real transcripts for your domain
2. **Set up CI/CD**: Run evaluation on every prompt change
3. **Track trends**: Monitor scores over time
4. **Optimize iteratively**: Improve prompt based on failing cases
5. **Deploy with confidence**: Ship only when scores improve

---

## Key Files to Remember

| File | Purpose |
|------|---------|
| `README.md` | Full documentation |
| `ARCHITECTURE.md` | Design deep-dive |
| `metrics.py` | How scores are calculated |
| `benchmark.py` | How tests are run |
| `transcripts/*.json` | Your test cases |
| `expected_outputs/*.json` | Reference answers |
| `results_*.json` | Your benchmark results |

---

## One-Liner Commands

```bash
# Run validation
cd backend && python -c "from evaluation.benchmark import BenchmarkSuite; suite = BenchmarkSuite('evaluation/transcripts', 'evaluation/expected_outputs'); suite.print_report(suite.run_all())"

# Export results
cd backend && python -c "from evaluation.benchmark import BenchmarkSuite; suite = BenchmarkSuite('evaluation/transcripts', 'evaluation/expected_outputs', 'v1.0'); results = suite.run_all(); suite.export_results(results, 'results_v1.0.json')"

# Compare versions
cd backend && python -c "import json; v1 = json.load(open('results_v1.0.json')); v2 = json.load(open('results_v1.1.json')); s1 = sum(r['overall_quality_score'] for r in v1['results'])/len(v1['results']); s2 = sum(r['overall_quality_score'] for r in v2['results'])/len(v2['results']); print(f'Improvement: {s2-s1:+.1f} points')"
```

---

**Ready to optimize your RELLS Engine prompt!**

Start with: `python -m pytest evaluation/test_evaluation.py::example_basic_evaluation -v`
