"""Benchmark suite for RELLS Engine evaluation.

Runs comprehensive tests against:
- Real transcripts
- Expected outputs
- Multiple prompt versions
- Detects regressions

Usage:
    benchmark = BenchmarkSuite("path/to/transcripts", "path/to/expected_outputs")
    results = benchmark.run_all()
    benchmark.print_report(results)
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import asdict

from .metrics import (
    MetricsCalculator, BenchmarkResult, ContractMetrics,
    TimestampMetrics, ViralityMetrics, GroundingMetrics, PerformanceMetrics
)


class BenchmarkSuite:
    """Runs benchmarks to measure RELLS Engine quality."""

    def __init__(
        self,
        transcripts_dir: str,
        expected_outputs_dir: str,
        prompt_version: str = "v1.0"
    ):
        """Initialize benchmark suite.

        Args:
            transcripts_dir: Path to directory with transcript JSON files
            expected_outputs_dir: Path to directory with expected output JSON files
            prompt_version: Version identifier for this prompt
        """
        self.transcripts_dir = Path(transcripts_dir)
        self.expected_outputs_dir = Path(expected_outputs_dir)
        self.prompt_version = prompt_version
        self.metrics_calc = MetricsCalculator()

    def load_transcript(self, filename: str) -> Optional[Dict]:
        """Load a transcript JSON file.

        Expected format:
        {
            "id": "test_001",
            "content": "Raw transcript text...",
            "duration_seconds": 600,
            "timestamps": [{"start": 0, "end": 30, "text": "..."}]
        }
        """
        filepath = self.transcripts_dir / filename
        if not filepath.exists():
            return None

        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_expected_output(self, filename: str) -> Optional[Dict]:
        """Load expected output JSON file.

        Expected format:
        {
            "test_id": "test_001",
            "most_relevant_segments": [
                {
                    "start_time": "00:15",
                    "end_time": "00:45",
                    "text": "...",
                    "virality": {"hook_score": 22, ...}
                }
            ]
        }
        """
        filepath = self.expected_outputs_dir / filename
        if not filepath.exists():
            return None

        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    def evaluate_single(
        self,
        test_name: str,
        output_json_str: str,
        transcript_text: str,
        expected_output: Dict,
        execution_time: float,
        model_used: str = "unknown",
        tokens_estimated: int = 0
    ) -> BenchmarkResult:
        """Evaluate a single test case.

        Args:
            test_name: Name of test
            output_json_str: LLM output as JSON string
            transcript_text: Original transcript
            expected_output: Expected/reference output
            execution_time: Time taken (seconds)
            model_used: Model identifier
            tokens_estimated: Estimated token count

        Returns:
            BenchmarkResult with all metrics
        """
        errors = []

        # Parse output JSON
        is_valid, output_json = self.metrics_calc.validate_json_structure(output_json_str)
        if not is_valid:
            errors.append("Invalid JSON in LLM output")
            output_json = {}

        # Get segment lists
        output_segments = output_json.get("most_relevant_segments", [])
        expected_segments = expected_output.get("most_relevant_segments", [])

        # Calculate metrics
        contract_metrics = self.metrics_calc.check_contract_compliance(output_json)
        timestamp_metrics = self.metrics_calc.calculate_timestamp_metrics(
            output_segments, expected_segments
        )
        virality_metrics = self.metrics_calc.calculate_virality_metrics(
            output_segments, expected_segments
        )
        grounding_metrics = self.metrics_calc.calculate_grounding_metrics(
            output_segments, transcript_text
        )
        performance_metrics = self.metrics_calc.calculate_performance_metrics(
            execution_time, len(output_segments), model_used, tokens_estimated
        )

        # Calculate overall score
        overall_score = self.metrics_calc.calculate_overall_quality_score(
            contract_metrics, timestamp_metrics, virality_metrics, grounding_metrics
        )

        return BenchmarkResult(
            test_name=test_name,
            transcript_length_chars=len(transcript_text),
            segments_generated=len(output_segments),
            contract_metrics=contract_metrics,
            timestamp_metrics=timestamp_metrics,
            virality_metrics=virality_metrics,
            grounding_metrics=grounding_metrics,
            performance_metrics=performance_metrics,
            overall_quality_score=overall_score,
            errors=errors
        )

    def run_all(
        self,
        ai_executor=None
    ) -> List[BenchmarkResult]:
        """Run all benchmarks in the test set.

        Args:
            ai_executor: Optional callable(transcript_text) -> json_string
                        If None, only validates against pre-computed expected outputs

        Returns:
            List of BenchmarkResult for each test
        """
        results = []

        # Find all transcript files
        transcript_files = sorted(self.transcripts_dir.glob("*.json"))

        for transcript_file in transcript_files:
            test_name = transcript_file.stem
            expected_file = test_name + ".json"

            # Load test data
            transcript_data = self.load_transcript(transcript_file.name)
            expected_output = self.load_expected_output(expected_file)

            if not transcript_data or not expected_output:
                continue

            transcript_text = transcript_data.get("content", "")

            # If executor provided, run LLM inference
            if ai_executor:
                start_time = time.time()
                try:
                    output_json_str = ai_executor(transcript_text)
                    execution_time = time.time() - start_time
                except Exception as e:
                    output_json_str = "{}"
                    execution_time = time.time() - start_time
                    errors = [f"Execution error: {str(e)}"]
            else:
                # Use pre-computed output from expected file
                output_json_str = json.dumps(expected_output)
                execution_time = 0.0

            # Evaluate
            result = self.evaluate_single(
                test_name=test_name,
                output_json_str=output_json_str,
                transcript_text=transcript_text,
                expected_output=expected_output,
                execution_time=execution_time
            )

            results.append(result)

        return results

    def compare_versions(
        self,
        results_v1: List[BenchmarkResult],
        results_v2: List[BenchmarkResult]
    ) -> Dict[str, Any]:
        """Compare results between two prompt versions.

        Args:
            results_v1: Results from version 1
            results_v2: Results from version 2

        Returns:
            Comparison metrics (regressions, improvements, etc.)
        """
        if not results_v1 or not results_v2:
            return {"error": "Cannot compare with empty result sets"}

        v1_scores = [r.overall_quality_score for r in results_v1]
        v2_scores = [r.overall_quality_score for r in results_v2]

        v1_mean = sum(v1_scores) / len(v1_scores)
        v2_mean = sum(v2_scores) / len(v2_scores)
        improvement = v2_mean - v1_mean

        # Detect regressions (score drop > 5%)
        regressions = []
        improvements_list = []

        for r1, r2 in zip(results_v1, results_v2):
            score_delta = r2.overall_quality_score - r1.overall_quality_score
            if score_delta < -5:
                regressions.append({
                    "test": r1.test_name,
                    "v1_score": r1.overall_quality_score,
                    "v2_score": r2.overall_quality_score,
                    "delta": score_delta
                })
            elif score_delta > 5:
                improvements_list.append({
                    "test": r1.test_name,
                    "v1_score": r1.overall_quality_score,
                    "v2_score": r2.overall_quality_score,
                    "delta": score_delta
                })

        return {
            "v1_mean_score": v1_mean,
            "v2_mean_score": v2_mean,
            "improvement_points": improvement,
            "improvement_percent": (improvement / v1_mean * 100) if v1_mean > 0 else 0,
            "regressions_detected": len(regressions) > 0,
            "regression_count": len(regressions),
            "regressions": regressions,
            "improvements": improvements_list,
            "tests_passed_v1": sum(1 for s in v1_scores if s >= 80),
            "tests_passed_v2": sum(1 for s in v2_scores if s >= 80),
            "total_tests": len(v1_scores)
        }

    def print_report(self, results: List[BenchmarkResult]) -> None:
        """Print formatted benchmark report.

        Args:
            results: BenchmarkResult list
        """
        if not results:
            print("No results to report")
            return

        print("\n" + "=" * 80)
        print(f"RELLS Engine Benchmark Report - {self.prompt_version}")
        print("=" * 80)

        # Summary statistics
        scores = [r.overall_quality_score for r in results]
        mean_score = sum(scores) / len(scores)
        min_score = min(scores)
        max_score = max(scores)

        print(f"\nOverall Quality Metrics:")
        print(f"  Mean Score:           {mean_score:.1f}/100")
        print(f"  Min Score:            {min_score:.1f}/100")
        print(f"  Max Score:            {max_score:.1f}/100")
        print(f"  Tests Passed (>=80):   {sum(1 for s in scores if s >= 80)}/{len(scores)}")

        # Aggregate metrics
        mean_timestamp_error = sum(r.timestamp_metrics.mean_error_seconds for r in results) / len(results)
        mean_virality_mae = sum(r.virality_metrics.mean_absolute_error for r in results) / len(results)
        mean_hallucination = sum(r.grounding_metrics.hallucination_rate for r in results) / len(results)
        mean_exec_time = sum(r.performance_metrics.execution_time_seconds for r in results) / len(results)
        total_cost = sum(r.performance_metrics.cost_usd_estimated for r in results)

        print(f"\nTimestamp Accuracy:")
        print(f"  Mean Error:           {mean_timestamp_error:.2f}s")
        print(f"  Within 2s:            {sum(r.timestamp_metrics.precision_within_2s for r in results) / len(results) * 100:.1f}%")

        print(f"\nVirality Scoring:")
        print(f"  Mean Absolute Error:  {mean_virality_mae:.2f}/100")
        print(f"  Mean Correlation:     {sum(r.virality_metrics.correlation_with_expected for r in results) / len(results):.3f}")

        print(f"\nContent Grounding:")
        print(f"  Hallucination Rate:   {mean_hallucination * 100:.1f}%")
        print(f"  Contract Compliance:  {sum(r.contract_metrics.required_fields_ratio for r in results) / len(results) * 100:.1f}%")

        print(f"\nPerformance:")
        print(f"  Mean Exec Time:       {mean_exec_time:.2f}s")
        print(f"  Total Est. Cost:      ${total_cost:.6f}")

        # Per-test breakdown
        print(f"\n{'-' * 80}")
        print(f"Per-Test Results:")
        print(f"{'-' * 80}")

        for result in results:
            status = "[PASS]" if result.overall_quality_score >= 80 else "[WARN]" if result.overall_quality_score >= 60 else "[FAIL]"
            print(f"\n{status} {result.test_name}")
            print(f"   Score: {result.overall_quality_score:.1f}/100")
            print(f"   Segments: {result.segments_generated}")
            print(f"   Timestamp Error: {result.timestamp_metrics.mean_error_seconds:.2f}s")
            print(f"   Virality MAE: {result.virality_metrics.mean_absolute_error:.2f}")
            print(f"   Hallucinations: {int(result.grounding_metrics.fabricated_quotes)}")

            if result.errors:
                print(f"   Errors: {', '.join(result.errors)}")

        print("\n" + "=" * 80)

    def export_results(self, results: List[BenchmarkResult], output_file: str) -> None:
        """Export results to JSON file.

        Args:
            results: BenchmarkResult list
            output_file: Path to write JSON results
        """
        export_data = {
            "prompt_version": self.prompt_version,
            "timestamp": str(time.time()),
            "results": [asdict(r) for r in results]
        }

        # Convert dataclasses to dicts
        for item in export_data["results"]:
            for key, value in item.items():
                if hasattr(value, "__dataclass_fields__"):
                    item[key] = asdict(value)

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, default=str)

        print(f"Results exported to {output_file}")
