# RELLS Engine Evaluation Framework

Comprehensive benchmark suite for measuring and comparing RELLS Engine prompt quality.

## Overview

The evaluation framework provides:

- **Quality Metrics**: Measure output contract compliance, timestamp accuracy, virality scoring, grounding
- **Performance Benchmarking**: Track execution time, token usage, estimated costs
- **Regression Detection**: Identify performance drops across prompt versions
- **Version Comparison**: Compare results between different prompt iterations
- **Objective Scoring**: 0-100 composite score with weighted metrics

## Architecture

```
backend/evaluation/
├── __init__.py              # Package marker
├── metrics.py               # Metric calculation engine
├── benchmark.py             # Benchmark suite
├── test_evaluation.py       # Usage examples
├── README.md                # This file
├── transcripts/
│   └── example_001.json     # Sample transcript
└── expected_outputs/
    └── example_001.json     # Reference/expected output
```

## Key Concepts

### Metrics

**Contract Compliance**
- Valid JSON output
- Required fields present
- Virality breakdown complete
- Timestamp ranges valid
- Segment duration within bounds (15-60s, prefer 25-50s)

**Timestamp Accuracy**
- Mean error (seconds)
- Precision within 2s, 5s
- No timestamps outside transcript

**Virality Scoring**
- Mean absolute error (MAE) vs expected scores
- Pearson correlation
- Sub-component accuracy (hook, engagement, value, shareability)
- Hook type distribution

**Grounding**
- Hallucination detection (text not in transcript)
- Fabricated quote count
- Content grounding violations

**Performance**
- Execution time (seconds)
- Time per segment (ms)
- Token usage (estimated)
- Cost estimation (USD)

### Composite Score (0-100)

Weighted calculation:
- Contract Compliance: 30%
- Timestamp Accuracy: 20%
- Virality Scoring: 25%
- Grounding/No Hallucinations: 25%

Score Interpretation:
- 80-100: Production ready ✅
- 60-79: Acceptable with caveats ⚠️
- 0-59: Needs improvement ❌

## Usage

### 1. Basic Evaluation

```python
from backend.evaluation.benchmark import BenchmarkSuite

# Initialize suite
suite = BenchmarkSuite(
    transcripts_dir="backend/evaluation/transcripts",
    expected_outputs_dir="backend/evaluation/expected_outputs",
    prompt_version="v1.0"
)

# Evaluate single test case
result = suite.evaluate_single(
    test_name="example_001",
    output_json_str=json.dumps(output),
    transcript_text=transcript,
    expected_output=expected,
    execution_time=2.5,
    model_used="claude-3.5-sonnet"
)

print(f"Quality Score: {result.overall_quality_score:.1f}/100")
```

### 2. Full Suite Benchmark

```python
# Without AI execution (validation only)
results = suite.run_all()

# With AI execution
def ai_executor(transcript_text: str) -> str:
    # Call RELLS Engine and return JSON
    # This would call the actual AI pipeline
    pass

results = suite.run_all(ai_executor=ai_executor)

# Print report
suite.print_report(results)

# Export results
suite.export_results(results, "results_v1.0.json")
```

### 3. Version Comparison

```python
# Load two sets of results
with open("results_v1.0.json") as f:
    v1_results = json.load(f)
with open("results_v1.1.json") as f:
    v2_results = json.load(f)

# Compare
comparison = suite.compare_versions(v1_results, v2_results)

print(f"Improvement: {comparison['improvement_percent']:.1f}%")
print(f"Regressions: {comparison['regression_count']}")

# Alert on regressions
if comparison['regressions_detected']:
    print("⚠️  Regressions detected:")
    for reg in comparison['regressions']:
        print(f"   {reg['test']}: {reg['v1_score']:.1f} → {reg['v2_score']:.1f}")
```

## Test Data Format

### Transcript Format

```json
{
  "id": "example_001",
  "title": "Optional title",
  "duration_seconds": 900,
  "content": "Full transcript text...",
  "speaker_labels": ["Speaker1", "Speaker2"]
}
```

### Expected Output Format

```json
{
  "test_id": "example_001",
  "most_relevant_segments": [
    {
      "start_time": "00:15",
      "end_time": "00:45",
      "text": "Segment text from transcript...",
      "relevance_score": 85,
      "reasoning": "Why this segment is relevant",
      "virality": {
        "hook_score": 22,
        "engagement_score": 20,
        "value_score": 23,
        "shareability_score": 21,
        "total_score": 86,
        "hook_type": "statement",
        "virality_reasoning": "Why this has high virality"
      },
      "hook_title": "3-9 word on-screen headline"
    }
  ],
  "summary": "Brief summary of content",
  "key_topics": ["Topic1", "Topic2"]
}
```

## Metrics in Detail

### Output Contract Compliance

Validates adherence to RELLS Engine v2.0 OUTPUT CONTRACT:

```
✅ Required fields: most_relevant_segments, summary, key_topics
✅ Each segment has: start_time, end_time, text, relevance_score, reasoning, virality, hook_title
✅ Virality breakdown: hook_score, engagement_score, value_score, shareability_score, total_score, hook_type, virality_reasoning
✅ Text field used (not "segment")
✅ Segment duration: 15-60s range
✅ Start < End timestamps
✅ Valid score ranges: subscores 0-25, total 0-100
```

### Timestamp Accuracy

Measures deviation from expected timestamps:

- **Mean Error**: Average absolute difference in seconds
- **Precision within 2s**: % of segments within ±2s
- **Precision within 5s**: % of segments within ±5s

Example:
```
Expected: 01:15 - 02:00
Output:   01:16 - 02:01
Error:    1.0 seconds
```

### Virality Scoring

Compares LLM virality scores against reference:

- **MAE**: How far off the virality scores are (lower = better)
- **Correlation**: How well scores align with expected ranking
- **Sub-component accuracy**: Per-dimension (hook, engagement, etc.)

Example:
```
Expected total: 86
Output total:   82
Error:          4 points
```

### Grounding (Hallucination Detection)

Checks if segment text appears in original transcript:

- **Hallucination Rate**: % of segments with text not in transcript
- **Fabricated Quotes**: Count of made-up quotes
- **Grounding Violations**: Detailed list of problems

Example:
```
✅ "AI makes doctors better" - Found in transcript
❌ "AI created 1M jobs in 2024" - Not in transcript (fabricated)
```

### Performance Metrics

Tracks operational characteristics:

- **Execution Time**: Wall-clock seconds for inference
- **Time per Segment**: ms per output segment
- **Token Estimation**: Approximate tokens consumed
- **Cost Estimation**: USD cost (based on provider rates)

Example:
```
Execution Time: 2.45 seconds
Segments: 4
Time per Segment: 612 ms/segment
Tokens: ~1200
Cost: $0.0012 (Claude 3.5 Sonnet rates)
```

## Integration with Pipeline

The evaluation framework is **separate from the production pipeline**. It:

- ✅ Does NOT modify `ai.py` or inference code
- ✅ Does NOT require running the full pipeline
- ✅ Can validate pre-computed outputs
- ✅ Can execute the pipeline and measure simultaneously
- ✅ Can compare different prompt versions

To integrate with the pipeline:

```python
from src.ai import get_most_relevant_parts_by_transcript
from evaluation.benchmark import BenchmarkSuite

suite = BenchmarkSuite(...)

def ai_executor(transcript_text: str) -> str:
    # Call actual pipeline
    result = get_most_relevant_parts_by_transcript(transcript_text)
    return json.dumps(result)

results = suite.run_all(ai_executor=ai_executor)
```

## Workflow

### 1. Establish Baseline

```bash
# Run evaluation with current prompt version
python -c "
from backend.evaluation.benchmark import BenchmarkSuite
suite = BenchmarkSuite('backend/evaluation/transcripts', 'backend/evaluation/expected_outputs', 'v1.0')
results = suite.run_all()  # Validation mode first
suite.print_report(results)
suite.export_results(results, 'baseline_v1.0.json')
"
```

### 2. Modify Prompt

Edit `backend/prompts/rells_engine.md` to improve the prompt

### 3. Test New Version

```bash
# Run evaluation with new prompt version
python -c "
from backend.evaluation.benchmark import BenchmarkSuite
suite = BenchmarkSuite('backend/evaluation/transcripts', 'backend/evaluation/expected_outputs', 'v1.1')
# results = suite.run_all(ai_executor=your_ai_function)  # Full execution
results = suite.run_all()  # Validation mode
suite.print_report(results)
suite.export_results(results, 'results_v1.1.json')
"
```

### 4. Compare Versions

```bash
# Detect regressions
python -c "
from backend.evaluation.benchmark import BenchmarkSuite
suite = BenchmarkSuite('backend/evaluation/transcripts', 'backend/evaluation/expected_outputs')
# Load results and compare
comparison = suite.compare_versions(v1_results, v1.1_results)
print(f'Improvement: {comparison[\"improvement_percent\"]:.1f}%')
if comparison['regressions_detected']:
    print('❌ Regressions found - do not ship')
"
```

## Adding Test Cases

### 1. Create Transcript

Save real transcript to `backend/evaluation/transcripts/your_test.json`:

```json
{
  "id": "your_test",
  "content": "Full transcript text...",
  "duration_seconds": 600
}
```

### 2. Create Expected Output

Create reference output in `backend/evaluation/expected_outputs/your_test.json`:

```json
{
  "test_id": "your_test",
  "most_relevant_segments": [
    { ... segment 1 ... },
    { ... segment 2 ... }
  ],
  "summary": "...",
  "key_topics": [...]
}
```

### 3. Run Evaluation

```python
suite = BenchmarkSuite(...)
results = suite.run_all()
# Your test case is automatically included
```

## Report Output Example

```
================================================================================
RELLS Engine Benchmark Report - v1.0
================================================================================

Overall Quality Metrics:
  Mean Score:           82.3/100
  Min Score:            76.5/100
  Max Score:            88.1/100
  Tests Passed (≥80):   3/4

Timestamp Accuracy:
  Mean Error:           1.45s
  Within 2s:            85.0%

Virality Scoring:
  Mean Absolute Error:  3.20/100
  Mean Correlation:     0.892

Content Grounding:
  Hallucination Rate:   5.0%
  Contract Compliance:  97.5%

Performance:
  Mean Exec Time:       2.34s
  Total Est. Cost:      $0.002450

────────────────────────────────────────────────────────────────────────────────
Per-Test Results:
────────────────────────────────────────────────────────────────────────────────

✅ example_001
   Score: 84.5/100
   Segments: 4
   Timestamp Error: 1.12s
   Virality MAE: 2.8
   Hallucinations: 0

```

## Extending Metrics

To add new metrics:

1. Add new `@dataclass` in `metrics.py`
2. Add calculation method to `MetricsCalculator`
3. Add to `BenchmarkResult`
4. Update composite score formula
5. Add to report printing

Example:

```python
@dataclass
class NewMetric:
    some_value: float

def calculate_new_metric(self, ...) -> NewMetric:
    # Implementation
    pass
```

## CI/CD Integration

Add to your CI pipeline:

```bash
# Check for regressions
python -c "
from backend.evaluation.benchmark import BenchmarkSuite
suite = BenchmarkSuite(...)
results = suite.run_all()
suite.export_results(results, 'latest_results.json')

# Compare with baseline
comparison = suite.compare_versions(baseline_results, results)
if comparison['regressions_detected']:
    exit(1)  # Fail CI
"
```

## Known Limitations

- Token estimation is approximate (varies by tokenizer)
- Cost estimation assumes specific provider rates (Claude 3.5 Sonnet)
- Hallucination detection requires transcript text matching (fuzzy matching not implemented)
- Correlation calculation uses Pearson (linear relationship assumed)

## Future Enhancements

- [ ] Fuzzy string matching for grounding detection
- [ ] Spacy/NLTK for semantic hallucination detection
- [ ] Multiple provider cost models
- [ ] A/B testing statistical significance
- [ ] Prompt modification tracking in git
- [ ] Visualization dashboard
- [ ] Real-time metric streaming

---

**Status**: Framework complete and ready for use  
**Last Updated**: 2026-08-06  
**Version**: 1.0  
