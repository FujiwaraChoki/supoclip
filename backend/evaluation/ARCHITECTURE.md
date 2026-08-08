# Evaluation Framework Architecture

## Design Philosophy

The evaluation framework is **orthogonal to the production pipeline**:

- ✅ Does NOT modify inference code
- ✅ Does NOT impact production performance
- ✅ Can run independently
- ✅ Enables A/B testing of prompt versions
- ✅ Provides objective quality metrics

## Component Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Evaluation Framework                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  BenchmarkSuite (benchmark.py)                             │
│  ├─ load_transcript()                                      │
│  ├─ load_expected_output()                                 │
│  ├─ evaluate_single()  ←─ MetricsCalculator              │
│  ├─ run_all()                                              │
│  ├─ compare_versions()                                     │
│  ├─ print_report()                                         │
│  └─ export_results()                                       │
│                                                             │
│  MetricsCalculator (metrics.py)                            │
│  ├─ validate_json_structure()                              │
│  ├─ check_contract_compliance()                            │
│  ├─ calculate_timestamp_metrics()                          │
│  ├─ calculate_virality_metrics()                           │
│  ├─ calculate_grounding_metrics()                          │
│  ├─ calculate_performance_metrics()                        │
│  └─ calculate_overall_quality_score()                      │
│                                                             │
│  Data Models (metrics.py - @dataclass)                    │
│  ├─ TimestampMetrics                                       │
│  ├─ ViralityMetrics                                        │
│  ├─ ContractMetrics                                        │
│  ├─ GroundingMetrics                                       │
│  ├─ PerformanceMetrics                                     │
│  └─ BenchmarkResult                                        │
│                                                             │
│  Test Data                                                  │
│  ├─ transcripts/ (JSON transcripts)                        │
│  ├─ expected_outputs/ (reference outputs)                  │
│  └─ [results files] (benchmarks)                           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
                          ↓
            (Optional Integration Point)
                          ↓
┌─────────────────────────────────────────────────────────────┐
│                  Production Pipeline                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  src/ai.py: get_most_relevant_parts_by_transcript()       │
│  ├─ Loads PromptManager.get("rells_engine")              │
│  ├─ Calls LLM (Claude, GPT, etc.)                         │
│  └─ Returns: most_relevant_segments (JSON)                │
│                                                             │
│  src/prompt_manager.py: PromptManager                      │
│  ├─ get("rells_engine") → prompt text                     │
│  ├─ Auto-reload via mtime                                  │
│  └─ In-memory caching                                      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Data Flow

### Single Test Evaluation

```
1. Load transcript (JSON)
   └─→ transcript_text: str

2. Call AI executor (or use pre-computed output)
   └─→ json_string: str

3. Parse and validate
   ├─→ valid_json_structure()
   ├─→ output_json: Dict

4. Calculate metrics in parallel
   ├─→ check_contract_compliance()
   ├─→ calculate_timestamp_metrics()
   ├─→ calculate_virality_metrics()
   ├─→ calculate_grounding_metrics()
   └─→ calculate_performance_metrics()

5. Combine metrics
   └─→ calculate_overall_quality_score()

6. Return result
   └─→ BenchmarkResult (includes all metrics + score)
```

### Full Suite Run

```
1. Discover test cases
   └─→ transcripts/*.json + expected_outputs/*.json

2. For each test case:
   ├─→ Load transcript
   ├─→ Load expected output
   ├─→ Execute AI (optional)
   ├─→ Evaluate (single test evaluation flow)
   └─→ Collect BenchmarkResult

3. Aggregate results
   └─→ List[BenchmarkResult]

4. Generate report
   └─→ Print summary stats + per-test breakdown
```

### Version Comparison

```
1. Load results from v1.0
   └─→ List[BenchmarkResult]

2. Load results from v1.1
   └─→ List[BenchmarkResult]

3. Pair-wise comparison
   ├─→ Calculate score deltas
   ├─→ Detect regressions (delta < -5)
   ├─→ Identify improvements (delta > 5)
   └─→ Calculate aggregate statistics

4. Generate comparison report
   └─→ Summary: improvement %, regression count
   └─→ Details: which tests regressed, by how much
```

## Metrics Hierarchy

```
BenchmarkResult (overall quality)
├─ Contract Compliance (30% weight)
│  ├─ Required fields ratio
│  ├─ Virality breakdown validity
│  ├─ Timestamp range validity
│  └─ Duration within bounds
│
├─ Timestamp Accuracy (20% weight)
│  ├─ Mean error (seconds)
│  ├─ Precision within 2s
│  └─ Precision within 5s
│
├─ Virality Scoring (25% weight)
│  ├─ Mean absolute error
│  ├─ Pearson correlation
│  ├─ Sub-component accuracy
│  └─ Hook type distribution
│
└─ Grounding (25% weight)
   ├─ Hallucination rate
   ├─ Fabricated quote count
   └─ Grounding violations
```

## Integration Points

### Option 1: Validation Only (Recommended for development)

No changes to pipeline. Just validate pre-computed outputs:

```python
suite = BenchmarkSuite(...)
results = suite.run_all()  # No ai_executor
# Uses output from expected_outputs/*.json
```

**Pros**:
- Fast feedback
- No pipeline dependencies
- Repeatable results

**Cons**:
- Doesn't measure actual execution time/cost
- Doesn't test full inference pipeline

### Option 2: Full Integration (For prompt optimization)

Add AI executor to measure real performance:

```python
def ai_executor(transcript_text: str) -> str:
    from src.ai import get_most_relevant_parts_by_transcript
    result = get_most_relevant_parts_by_transcript(transcript_text)
    return json.dumps(result)

suite = BenchmarkSuite(...)
results = suite.run_all(ai_executor=ai_executor)
```

**Pros**:
- Measures actual execution time/cost
- Tests real inference pipeline
- Catches integration issues

**Cons**:
- Slower (requires actual LLM calls)
- Cost implications (API charges)
- Rate limits

## File Formats

### Input: Transcript (transcripts/*.json)

```json
{
  "id": "unique_id",
  "title": "Optional",
  "duration_seconds": 900,
  "content": "Full transcript text",
  "speaker_labels": ["Speaker1", "Speaker2"],
  // Optional fields for context
  "url": "https://...",
  "date": "2024-01-15"
}
```

### Input: Expected Output (expected_outputs/*.json)

```json
{
  "test_id": "matches_transcript_id",
  "most_relevant_segments": [...],
  "summary": "...",
  "key_topics": [...]
}
```

### Output: Results (results_*.json)

```json
{
  "prompt_version": "v1.0",
  "timestamp": "1699564800.123",
  "results": [
    {
      "test_name": "example_001",
      "overall_quality_score": 84.5,
      "contract_metrics": {...},
      "timestamp_metrics": {...},
      "virality_metrics": {...},
      "grounding_metrics": {...},
      "performance_metrics": {...},
      "errors": []
    }
  ]
}
```

## Measurement Methodology

### Contract Compliance

**Method**: Pattern matching against OUTPUT CONTRACT requirements

```
✅ Required field present?
✅ Correct data type?
✅ Value in valid range?
✅ Nested structure correct?
```

**Example**:
```
Checking segment virality breakdown:
├─ hook_score in [0, 25]? ✅
├─ engagement_score in [0, 25]? ✅
├─ value_score in [0, 25]? ✅
├─ shareability_score in [0, 25]? ✅
└─ total_score == sum? ✅

Result: valid → +25 points
```

### Timestamp Accuracy

**Method**: Compare output timestamps against expected timestamps

```
Expected: "00:15" - "00:45" (30s duration)
Output:   "00:16" - "00:46" (30s duration)

Error calculation:
├─ Start error: |16 - 15| = 1s
├─ End error: |46 - 45| = 1s
└─ Mean error: (1 + 1) / 2 = 1.0s

Precision:
├─ Within 2s? Yes → +1
├─ Within 5s? Yes → +1
```

### Virality Scoring

**Method**: Compare numerical scores using statistical measures

```
Expected: [86, 83, 90, 78] (example scores)
Output:   [84, 82, 88, 80]

Calculations:
├─ Absolute errors: [2, 1, 2, 2]
├─ MAE: (2+1+2+2)/4 = 1.75
├─ Pearson r: 0.98 (very high correlation)

Result: High accuracy
```

### Grounding (Hallucination Detection)

**Method**: Substring search in original transcript

```
For each segment's "text" field:
├─ Is exact text in transcript? Yes → ✅
├─ Is exact text in transcript? No → ❌ hallucination
└─ Count hallucinations

Result: hallucination_rate = hallucinations / total_segments
```

**Limitations**:
- Requires exact substring match
- Doesn't handle paraphrasing
- Doesn't detect fabricated statistics

**Future**: Could use NLP for semantic similarity

### Performance Metrics

**Method**: Measure wall-clock time and estimate costs

```
Measurement:
├─ time_start = time.time()
├─ result = ai_executor(transcript)
├─ time_end = time.time()
└─ execution_time = time_end - time_start

Token Estimation:
├─ Input tokens: len(prompt) + len(transcript)
├─ Output tokens: len(json_result)
└─ Rough estimate (actual depends on tokenizer)

Cost Calculation:
└─ tokens * (USD_per_token) = cost_usd
```

## Extensibility

### Adding a New Metric

1. **Define data class** (metrics.py):
```python
@dataclass
class MyMetric:
    value1: float
    value2: int
```

2. **Add calculation method** (MetricsCalculator):
```python
@staticmethod
def calculate_my_metric(...) -> MyMetric:
    # Implementation
    return MyMetric(...)
```

3. **Integrate into BenchmarkResult**:
```python
@dataclass
class BenchmarkResult:
    # ... existing fields ...
    my_metrics: MyMetric
```

4. **Update composite score** (if needed):
```python
my_score = calculate_my_metric_score(my_metrics)
overall = (existing_score * 0.75) + (my_score * 0.25)
```

5. **Add to reporting**:
```python
def print_report(self, results):
    # ... existing prints ...
    print(f"My Metric: {result.my_metrics.value1}")
```

### Adding a New Test Case

1. **Create transcript**: `transcripts/my_test.json`
2. **Create expected output**: `expected_outputs/my_test.json`
3. **Run evaluation**: `suite.run_all()` (automatically discovers)

## Performance Characteristics

```
Single Test Evaluation:
├─ Parse JSON: ~1ms
├─ Validate contract: ~5ms
├─ Calculate timestamp metrics: ~2ms
├─ Calculate virality metrics: ~2ms
├─ Calculate grounding metrics: ~3ms
├─ Calculate performance metrics: ~1ms
└─ Total: ~15ms (without AI call)

Full Suite (10 test cases):
├─ Load data: ~50ms
├─ Evaluate all: ~150ms
├─ Generate report: ~20ms
└─ Total: ~220ms (without AI calls)

With AI Execution (per test):
├─ Framework overhead: ~15ms
├─ AI inference: ~2000-5000ms (varies by model/complexity)
└─ Total per test: ~2000-5000ms
```

## Regression Detection Strategy

```
Algorithm: Compare versions

1. Run full suite on v1.0
   └─→ results_v1.0.json

2. Run full suite on v1.1
   └─→ results_v1.1.json

3. For each test case:
   ├─→ Calculate score delta: v1.1_score - v1.0_score
   ├─→ If delta < -5: REGRESSION
   └─→ If delta > 5: IMPROVEMENT

4. Aggregate:
   ├─→ Total regressions: count(delta < -5)
   ├─→ Mean delta: sum(all_deltas) / count
   └─→ Decision: Ship? Only if no regressions

Example:
test_001: 82.0 → 80.5 = -1.5 (OK)
test_002: 78.0 → 70.0 = -8.0 (REGRESSION! ❌)
test_003: 85.0 → 88.0 = +3.0 (OK)

Result: REGRESSION DETECTED → Block shipping
```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: RELLS Engine Evaluation

on: [pull_request]

jobs:
  evaluate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Run Benchmark Suite
        run: |
          cd backend
          python -m pytest evaluation/test_evaluation.py -v
      
      - name: Compare with Baseline
        run: |
          python -c "
          from evaluation.benchmark import BenchmarkSuite
          suite = BenchmarkSuite(...)
          results = suite.run_all()
          comparison = suite.compare_versions(baseline, results)
          if comparison['regressions_detected']:
            print('❌ Regressions found')
            exit(1)
          "
      
      - name: Upload Results
        uses: actions/upload-artifact@v3
        with:
          name: benchmark-results
          path: backend/evaluation/results_*.json
```

## Monitoring and Alerting

```
Metrics to track over time:
├─ Mean quality score (trend)
├─ Regression count (threshold: > 0)
├─ Mean execution time (threshold: < 5s)
├─ Cost per call (threshold: < $0.01)
├─ Hallucination rate (threshold: < 10%)
└─ Contract compliance (threshold: > 95%)

Alert conditions:
├─ Quality score ↓ > 5 points
├─ Hallucination rate ↑ > 5%
├─ Execution time ↑ > 2x
└─ Any regression on critical tests
```

## Security Considerations

- ✅ Framework doesn't handle sensitive data (transcripts are test data only)
- ✅ No credentials stored (API keys handled by pipeline)
- ✅ Results are JSON files (no SQL injection risks)
- ⚠️ Cost estimation is approximate (don't use for billing)

---

**Architecture Version**: 1.0  
**Status**: Final  
**Last Updated**: 2026-08-06
