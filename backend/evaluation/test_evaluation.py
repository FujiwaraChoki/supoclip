"""Example usage of the evaluation framework.

This shows how to:
1. Load test cases
2. Run benchmark suite
3. Compare prompt versions
4. Generate reports
"""

import json
from pathlib import Path
from .benchmark import BenchmarkSuite


def example_basic_evaluation():
    """Example: Basic evaluation of a test case."""
    suite = BenchmarkSuite(
        transcripts_dir="backend/evaluation/transcripts",
        expected_outputs_dir="backend/evaluation/expected_outputs",
        prompt_version="v1.0"
    )

    # Load test data
    transcript = suite.load_transcript("example_001.json")
    expected = suite.load_expected_output("example_001.json")

    if not transcript or not expected:
        print("Test data not found")
        return

    # Simulate LLM output (would come from actual AI call)
    simulated_output = {
        "most_relevant_segments": [
            {
                "start_time": "01:15",
                "end_time": "02:00",
                "text": "AI will augment human capabilities.",
                "relevance_score": 85,
                "reasoning": "Good example",
                "virality": {
                    "hook_score": 22,
                    "engagement_score": 20,
                    "value_score": 23,
                    "shareability_score": 21,
                    "total_score": 86,
                    "hook_type": "statement",
                    "virality_reasoning": "Strong hook"
                },
                "hook_title": "AI makes doctors better"
            }
        ],
        "summary": "AI augmentation discussion",
        "key_topics": ["AI", "Jobs"]
    }

    # Evaluate
    result = suite.evaluate_single(
        test_name="example_001",
        output_json_str=json.dumps(simulated_output),
        transcript_text=transcript["content"],
        expected_output=expected,
        execution_time=2.5,
        model_used="claude-3.5-sonnet"
    )

    print(f"\n✅ Test: {result.test_name}")
    print(f"   Quality Score: {result.overall_quality_score:.1f}/100")
    print(f"   Segments: {result.segments_generated}")
    print(f"   Timestamp Accuracy: {result.timestamp_metrics.precision_within_2s * 100:.1f}%")
    print(f"   Hallucination Rate: {result.grounding_metrics.hallucination_rate * 100:.1f}%")
    print(f"   Execution Time: {result.performance_metrics.execution_time_seconds:.2f}s")


def example_full_suite():
    """Example: Run full benchmark suite."""
    suite = BenchmarkSuite(
        transcripts_dir="backend/evaluation/transcripts",
        expected_outputs_dir="backend/evaluation/expected_outputs",
        prompt_version="v1.0"
    )

    # If you have an AI executor:
    # def ai_executor(transcript_text: str) -> str:
    #     # Call RELLS Engine and return JSON
    #     pass
    # results = suite.run_all(ai_executor=ai_executor)

    # For now, just validate without executing AI
    results = suite.run_all(ai_executor=None)

    suite.print_report(results)
    suite.export_results(results, "backend/evaluation/results_v1.0.json")


def example_version_comparison():
    """Example: Compare two prompt versions."""
    # Load results from two runs
    with open("backend/evaluation/results_v1.0.json") as f:
        v1_data = json.load(f)
    with open("backend/evaluation/results_v1.1.json") as f:
        v2_data = json.load(f)

    # (This is pseudocode - would need to reconstruct BenchmarkResult objects)
    # comparison = suite.compare_versions(results_v1, results_v2)

    # Print comparison
    print("\n📊 Version Comparison: v1.0 → v1.1")
    print("=" * 60)
    # print(f"Score Improvement: {comparison['improvement_points']:.1f} points "
    #       f"({comparison['improvement_percent']:.1f}%)")
    # print(f"Regressions: {comparison['regression_count']}")
    # print(f"Tests Passed: {comparison['tests_passed_v1']} → {comparison['tests_passed_v2']}")


if __name__ == "__main__":
    print("RELLS Engine Evaluation Framework Examples")
    print("=" * 60)

    print("\n1. Running basic evaluation...")
    try:
        example_basic_evaluation()
    except Exception as e:
        print(f"   Error: {e}")

    print("\n2. Running full suite...")
    try:
        example_full_suite()
    except Exception as e:
        print(f"   Error: {e}")

    print("\n3. Version comparison...")
    print("   (Requires pre-computed results files)")
    # example_version_comparison()
