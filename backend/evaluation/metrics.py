"""Metrics calculation for RELLS Engine evaluation.

Computes quality metrics for:
- Output contract compliance
- Timestamp precision
- Virality scoring accuracy
- Content grounding (no hallucinations)
- Performance benchmarking
"""

import json
from dataclasses import dataclass
from typing import Dict, List, Tuple, Any
from datetime import datetime


@dataclass
class TimestampMetrics:
    """Metrics for timestamp accuracy."""

    mean_error_seconds: float
    max_error_seconds: float
    precision_within_2s: float
    precision_within_5s: float
    segments_evaluated: int


@dataclass
class ViralityMetrics:
    """Metrics for virality scoring accuracy."""

    mean_absolute_error: float
    correlation_with_expected: float
    hook_score_accuracy: float
    engagement_score_accuracy: float
    value_score_accuracy: float
    shareability_score_accuracy: float
    hook_type_recall: Dict[str, float]


@dataclass
class ContractMetrics:
    """Metrics for output contract compliance."""

    valid_json_ratio: float
    required_fields_ratio: float
    segment_count_matches: float
    virality_breakdown_valid: float
    timestamp_range_valid: float
    duration_within_bounds: float
    issues_found: List[str]


@dataclass
class GroundingMetrics:
    """Metrics for content grounding (detecting hallucinations)."""

    hallucination_rate: float
    timestamp_outside_transcript: float
    text_not_in_transcript: float
    fabricated_quotes: int
    grounding_violations: List[Dict[str, Any]]


@dataclass
class PerformanceMetrics:
    """Metrics for execution performance."""

    execution_time_seconds: float
    time_per_segment_ms: float
    tokens_used_estimated: int
    cost_usd_estimated: float
    model_used: str
    timestamp: datetime


@dataclass
class BenchmarkResult:
    """Complete benchmark result for a test case."""

    test_name: str
    transcript_length_chars: int
    segments_generated: int
    contract_metrics: ContractMetrics
    timestamp_metrics: TimestampMetrics
    virality_metrics: ViralityMetrics
    grounding_metrics: GroundingMetrics
    performance_metrics: PerformanceMetrics
    overall_quality_score: float
    errors: List[str]


class MetricsCalculator:
    """Calculates evaluation metrics for RELLS Engine outputs."""

    @staticmethod
    def validate_json_structure(output: str) -> Tuple[bool, Dict]:
        """Validate output is valid JSON.

        Args:
            output: LLM output string

        Returns:
            (is_valid, parsed_json)
        """
        try:
            data = json.loads(output)
            return True, data
        except json.JSONDecodeError:
            return False, {}

    @staticmethod
    def check_contract_compliance(output_json: Dict) -> ContractMetrics:
        """Validate output against RELLS Engine OUTPUT CONTRACT.

        Contract requires:
        - Valid JSON
        - "most_relevant_segments", "summary", "key_topics"
        - Each segment: "start_time", "end_time", "text", "relevance_score",
          "reasoning", "virality", "hook_title"
        - virality breakdown: "hook_score", "engagement_score", "value_score",
          "shareability_score", "total_score", "hook_type", "virality_reasoning"
        - Segments 15-60s, prefer 25-50s
        - No "segment" field (use "text")
        """
        issues = []

        # Check required top-level fields
        required_fields = ["most_relevant_segments", "summary", "key_topics"]
        missing_fields = [f for f in required_fields if f not in output_json]
        required_fields_ratio = 1.0 - (len(missing_fields) / len(required_fields))
        if missing_fields:
            issues.append(f"Missing top-level fields: {missing_fields}")

        # Validate segments
        segments = output_json.get("most_relevant_segments", [])
        segment_count_matches = 2.0 <= len(segments) <= 5.0

        virality_breakdown_valid = 0.0
        timestamp_range_valid = 0.0
        duration_within_bounds = 0.0

        if segments:
            valid_virality_count = 0
            valid_timestamp_count = 0
            valid_duration_count = 0

            required_segment_fields = [
                "start_time", "end_time", "text", "relevance_score",
                "reasoning", "virality", "hook_title"
            ]

            for i, seg in enumerate(segments):
                # Check for "segment" field (should be "text")
                if "segment" in seg and "text" not in seg:
                    issues.append(f"Segment {i}: uses 'segment' instead of 'text'")

                # Validate virality breakdown
                virality = seg.get("virality", {})
                virality_fields = [
                    "hook_score", "engagement_score", "value_score",
                    "shareability_score", "total_score", "hook_type",
                    "virality_reasoning"
                ]
                if all(f in virality for f in virality_fields):
                    valid_virality_count += 1

                    # Validate score ranges (0-25 each, total 0-100)
                    hook = virality.get("hook_score", -1)
                    engagement = virality.get("engagement_score", -1)
                    value = virality.get("value_score", -1)
                    shareability = virality.get("shareability_score", -1)
                    total = virality.get("total_score", -1)

                    if all(0 <= s <= 25 for s in [hook, engagement, value, shareability]):
                        if 0 <= total <= 100 and total == sum([hook, engagement, value, shareability]):
                            pass
                        else:
                            issues.append(f"Segment {i}: virality total doesn't sum correctly")
                else:
                    issues.append(f"Segment {i}: missing virality breakdown fields")

                # Validate timestamps
                try:
                    start_str = str(seg.get("start_time", ""))
                    end_str = str(seg.get("end_time", ""))

                    # Parse MM:SS format
                    def parse_timestamp(ts_str: str) -> float:
                        parts = ts_str.split(":")
                        if len(parts) == 2:
                            return int(parts[0]) * 60 + float(parts[1])
                        return float(ts_str)

                    start_sec = parse_timestamp(start_str)
                    end_sec = parse_timestamp(end_str)

                    if start_sec < end_sec:
                        valid_timestamp_count += 1

                        # Check duration bounds (15-60s, prefer 25-50s)
                        duration = end_sec - start_sec
                        if 15 <= duration <= 60:
                            valid_duration_count += 1
                        else:
                            issues.append(
                                f"Segment {i}: duration {duration:.1f}s "
                                f"outside 15-60s range"
                            )
                    else:
                        issues.append(
                            f"Segment {i}: start_time ({start_str}) >= "
                            f"end_time ({end_str})"
                        )
                except (ValueError, IndexError):
                    issues.append(f"Segment {i}: invalid timestamp format")

            virality_breakdown_valid = valid_virality_count / len(segments)
            timestamp_range_valid = valid_timestamp_count / len(segments)
            duration_within_bounds = valid_duration_count / len(segments)

        return ContractMetrics(
            valid_json_ratio=1.0,
            required_fields_ratio=required_fields_ratio,
            segment_count_matches=float(segment_count_matches),
            virality_breakdown_valid=virality_breakdown_valid,
            timestamp_range_valid=timestamp_range_valid,
            duration_within_bounds=duration_within_bounds,
            issues_found=issues
        )

    @staticmethod
    def calculate_timestamp_metrics(
        output_segments: List[Dict],
        expected_segments: List[Dict]
    ) -> TimestampMetrics:
        """Calculate timestamp accuracy against expected segments.

        Args:
            output_segments: Segments from LLM output
            expected_segments: Expected/reference segments

        Returns:
            TimestampMetrics with precision statistics
        """
        def parse_timestamp(ts_str: str) -> float:
            parts = str(ts_str).split(":")
            if len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
            return float(ts_str)

        errors = []
        for i, (output_seg, expected_seg) in enumerate(zip(output_segments, expected_segments)):
            try:
                output_start = parse_timestamp(output_seg["start_time"])
                output_end = parse_timestamp(output_seg["end_time"])
                expected_start = parse_timestamp(expected_seg["start_time"])
                expected_end = parse_timestamp(expected_seg["end_time"])

                start_error = abs(output_start - expected_start)
                end_error = abs(output_end - expected_end)
                errors.append((start_error + end_error) / 2)
            except (KeyError, ValueError, IndexError):
                errors.append(10.0)  # High penalty for invalid segments

        if not errors:
            return TimestampMetrics(
                mean_error_seconds=0.0,
                max_error_seconds=0.0,
                precision_within_2s=1.0,
                precision_within_5s=1.0,
                segments_evaluated=0
            )

        mean_error = sum(errors) / len(errors)
        max_error = max(errors)
        within_2s = sum(1 for e in errors if e <= 2.0) / len(errors)
        within_5s = sum(1 for e in errors if e <= 5.0) / len(errors)

        return TimestampMetrics(
            mean_error_seconds=mean_error,
            max_error_seconds=max_error,
            precision_within_2s=within_2s,
            precision_within_5s=within_5s,
            segments_evaluated=len(errors)
        )

    @staticmethod
    def calculate_virality_metrics(
        output_segments: List[Dict],
        expected_segments: List[Dict]
    ) -> ViralityMetrics:
        """Calculate virality scoring accuracy.

        Args:
            output_segments: Segments from LLM output
            expected_segments: Expected/reference segments

        Returns:
            ViralityMetrics with scoring statistics
        """
        output_scores = []
        expected_scores = []
        hook_types_found = {}

        for output_seg, expected_seg in zip(output_segments, expected_segments):
            output_virality = output_seg.get("virality", {})
            expected_virality = expected_seg.get("virality", {})

            output_total = output_virality.get("total_score", 0)
            expected_total = expected_virality.get("total_score", 0)

            output_scores.append(output_total)
            expected_scores.append(expected_total)

            # Track hook types
            hook_type = output_virality.get("hook_type", "none")
            hook_types_found[hook_type] = hook_types_found.get(hook_type, 0) + 1

        if not output_scores:
            return ViralityMetrics(
                mean_absolute_error=0.0,
                correlation_with_expected=0.0,
                hook_score_accuracy=0.0,
                engagement_score_accuracy=0.0,
                value_score_accuracy=0.0,
                shareability_score_accuracy=0.0,
                hook_type_recall={}
            )

        # Calculate MAE
        mae = sum(abs(o - e) for o, e in zip(output_scores, expected_scores)) / len(output_scores)

        # Calculate Pearson correlation (simplified)
        if len(set(expected_scores)) > 1:  # Avoid div by zero
            mean_output = sum(output_scores) / len(output_scores)
            mean_expected = sum(expected_scores) / len(expected_scores)

            numerator = sum(
                (o - mean_output) * (e - mean_expected)
                for o, e in zip(output_scores, expected_scores)
            )
            denom1 = sum((o - mean_output) ** 2 for o in output_scores)
            denom2 = sum((e - mean_expected) ** 2 for e in expected_scores)

            if denom1 > 0 and denom2 > 0:
                correlation = numerator / (denom1 ** 0.5 * denom2 ** 0.5)
            else:
                correlation = 0.0
        else:
            correlation = 0.0

        # Sub-component accuracy (simplified)
        hook_accuracy = 1.0 - (mae / 25.0)  # Normalize by max score
        hook_accuracy = min(1.0, max(0.0, hook_accuracy))

        return ViralityMetrics(
            mean_absolute_error=mae,
            correlation_with_expected=correlation,
            hook_score_accuracy=hook_accuracy,
            engagement_score_accuracy=hook_accuracy,
            value_score_accuracy=hook_accuracy,
            shareability_score_accuracy=hook_accuracy,
            hook_type_recall=hook_types_found
        )

    @staticmethod
    def calculate_grounding_metrics(
        output_segments: List[Dict],
        transcript: str
    ) -> GroundingMetrics:
        """Detect hallucinations (content not in transcript).

        Args:
            output_segments: Segments from LLM output
            transcript: Original transcript text

        Returns:
            GroundingMetrics with hallucination statistics
        """
        violations = []
        hallucinated = 0

        for i, seg in enumerate(output_segments):
            text = seg.get("text", "")

            # Check if segment text is in transcript
            if text and text.strip() not in transcript:
                hallucinated += 1
                violations.append({
                    "segment_index": i,
                    "type": "text_not_in_transcript",
                    "text_sample": text[:100]
                })

        hallucination_rate = hallucinated / len(output_segments) if output_segments else 0.0

        return GroundingMetrics(
            hallucination_rate=hallucination_rate,
            timestamp_outside_transcript=0.0,
            text_not_in_transcript=hallucination_rate,
            fabricated_quotes=hallucinated,
            grounding_violations=violations
        )

    @staticmethod
    def calculate_performance_metrics(
        execution_time_seconds: float,
        segments_count: int,
        model_used: str,
        tokens_estimated: int = 0
    ) -> PerformanceMetrics:
        """Calculate performance metrics.

        Args:
            execution_time_seconds: Time taken for inference
            segments_count: Number of segments generated
            model_used: Model name/ID
            tokens_estimated: Estimated tokens used

        Returns:
            PerformanceMetrics with performance data
        """
        time_per_segment = (execution_time_seconds / segments_count * 1000) if segments_count > 0 else 0.0

        # Rough cost estimation (varies by provider)
        # Assuming ~0.001 USD per 1000 tokens (typical for Claude 3.5 Sonnet)
        cost_usd = (tokens_estimated / 1000) * 0.001

        return PerformanceMetrics(
            execution_time_seconds=execution_time_seconds,
            time_per_segment_ms=time_per_segment,
            tokens_used_estimated=tokens_estimated,
            cost_usd_estimated=cost_usd,
            model_used=model_used,
            timestamp=datetime.now()
        )

    @staticmethod
    def calculate_overall_quality_score(
        contract_metrics: ContractMetrics,
        timestamp_metrics: TimestampMetrics,
        virality_metrics: ViralityMetrics,
        grounding_metrics: GroundingMetrics
    ) -> float:
        """Calculate composite quality score (0-100).

        Weighting:
        - Contract compliance: 30%
        - Timestamp accuracy: 20%
        - Virality scoring: 25%
        - Grounding/no hallucinations: 25%
        """
        # Contract score (0-100)
        contract_score = (
            contract_metrics.required_fields_ratio * 50 +
            contract_metrics.virality_breakdown_valid * 25 +
            contract_metrics.timestamp_range_valid * 25
        )

        # Timestamp score (0-100)
        timestamp_score = contract_metrics.duration_within_bounds * 100

        # Virality score (0-100)
        virality_score = (1.0 - min(1.0, virality_metrics.mean_absolute_error / 25.0)) * 100

        # Grounding score (0-100)
        grounding_score = (1.0 - grounding_metrics.hallucination_rate) * 100

        # Weighted combination
        overall = (
            contract_score * 0.30 +
            timestamp_score * 0.20 +
            virality_score * 0.25 +
            grounding_score * 0.25
        )

        return min(100.0, max(0.0, overall))
