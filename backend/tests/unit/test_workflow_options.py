"""Workflow request validation, contracts and lengths (spec sections 1.1, 2, 4.2)."""

import hashlib
import json
import pickle
from unittest.mock import MagicMock

import pytest

from src.workflows import durations
from src.workflows.contracts import (
    OUTLINE_VERSION,
    WORKFLOW_PLAN_VERSION,
    WORKFLOWS,
    WorkflowOptions,
    WorkflowResult,
    WorkflowSpec,
    workflow_spec_from_task,
)
from src.workflows.errors import WorkflowError, WorkflowRequestError
from src.workflows.options import (
    INSTRUCTIONS_MAX_CHARS,
    TOPIC_MAX_CHARS,
    check_source_duration,
    normalize_text,
    parse_workflow_request,
    workflow_not_available,
    workflow_runtime_minimum_error,
    workflows_info,
)


def parse(body, clip_length="auto"):
    return parse_workflow_request(body, clip_length)


def rejected(body, clip_length="auto") -> WorkflowRequestError:
    with pytest.raises(WorkflowRequestError) as error:
        parse(body, clip_length)
    return error.value


# --- defaults ---------------------------------------------------------------


def test_missing_workflow_is_plain_highlights():
    spec = parse({"source": {"url": "https://youtu.be/x"}})
    assert spec == WorkflowSpec("highlights", WorkflowOptions(), "auto")
    assert spec.legacy is True
    assert spec.options.to_json() == {}
    assert parse({"workflow": None}).legacy is True


def test_highlights_with_instructions_is_not_legacy():
    spec = parse({"instructions": "  Skip the sponsor read  "})
    assert spec.workflow == "highlights"
    assert spec.options.instructions == "Skip the sponsor read"
    assert spec.legacy is False
    assert spec.uses_performance_context is True


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_every_workflow_parses(workflow):
    body = {"workflow": workflow}
    if workflow == "topic":
        body["workflow_options"] = {"topic": "pricing"}
    spec = parse(body, "medium")
    assert spec.workflow == workflow
    assert spec.clip_length == "medium"
    assert spec.uses_performance_context is (workflow in ("highlights", "topic"))


# --- 422 codes ----------------------------------------------------------------


@pytest.mark.parametrize("value", ["Series", "series ", "bogus", 1, True, [], {"id": "series"}, ""])
def test_unknown_workflow_is_rejected_never_coerced(value):
    error = rejected({"workflow": value})
    assert (error.code, error.status) == ("INVALID_WORKFLOW", 422)
    assert error.message == "Unknown workflow. Use highlights, series, topic, recap or trailer."


@pytest.mark.parametrize("value", ["topic", 3, ["a"]])
def test_workflow_options_must_be_an_object(value):
    error = rejected({"workflow": "series", "workflow_options": value})
    assert (error.code, error.status) == ("INVALID_WORKFLOW_OPTIONS", 422)


@pytest.mark.parametrize(
    "options",
    [
        {}, {"topic": ""}, {"topic": "  "}, {"topic": "a"}, {"topic": 42}, {"topic": None},
        # Nothing searchable: punctuation, invisible characters, a lone surrogate.
        {"topic": "!!"}, {"topic": "?"}, {"topic": "\u200b\u200b"}, {"topic": "\ufeff\u00ad"}, {"topic": "\ud800\udfff"},
    ],
)
def test_topic_workflow_needs_a_topic(options):
    error = rejected({"workflow": "topic", "workflow_options": options})
    assert (error.code, error.message) == ("TOPIC_REQUIRED", "Enter a topic to look for.")


@pytest.mark.parametrize("topic", ["钱", "猫", "お", "돈", "\U00020000", "AI", "C#", "5G", "धन"])
def test_short_topics_that_are_words_are_accepted(topic):
    assert parse({"workflow": "topic", "workflow_options": {"topic": topic}}).options.topic == topic


def test_topic_drops_invisible_characters_and_surrogates():
    topic = "pric\u00ading\u200b stra\ufefftegy\ud800"
    spec = parse({"workflow": "topic", "workflow_options": {"topic": topic}, "instructions": "calm\udfff"})
    assert (spec.options.topic, spec.options.instructions) == ("pricing strategy", "calm")
    # Everything stored or hashed is valid UTF-8.
    json.dumps(spec.options.to_json(), ensure_ascii=False).encode("utf-8")
    assert len(spec.fingerprint()) == 64


def test_topic_length_limit_counts_after_normalization():
    assert parse({"workflow": "topic", "workflow_options": {"topic": "x" * TOPIC_MAX_CHARS}}).options.topic == "x" * 120
    padded = "  " + "word  " * 20 + "\t"
    assert len(parse({"workflow": "topic", "workflow_options": {"topic": padded}}).options.topic) == 99
    error = rejected({"workflow": "topic", "workflow_options": {"topic": "x" * 121}})
    assert (error.code, error.message) == ("TOPIC_TOO_LONG", "Keep the topic under 120 characters.")


def test_topic_is_normalized_and_dropped_for_other_workflows():
    spec = parse({"workflow": "topic", "workflow_options": {"topic": " building\n\ta\x00 business‮ "}})
    assert spec.options.topic == "building a business"
    for workflow in ("highlights", "series", "recap", "trailer"):
        other = parse({"workflow": workflow, "workflow_options": {"topic": "pricing"}})
        assert other.options.topic == ""
        assert "topic" not in other.options.to_json()


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("auto", None), ("AUTO", None), (" auto ", None), (2, 2), (10, 10), ("4", 4), (" 6 ", 6), (5.0, 5)],
)
def test_part_count_parsing(value, expected):
    assert parse({"workflow": "series", "workflow_options": {"part_count": value}}).options.part_count == expected


def test_missing_part_count_is_auto():
    assert parse({"workflow": "series"}).options.part_count is None


@pytest.mark.parametrize("value", [1, 11, 0, -2, "11", "two", "4.5", 4.5, True, False, [], {}, "", float("nan")])
def test_invalid_part_count_is_rejected(value):
    error = rejected({"workflow": "series", "workflow_options": {"part_count": value}})
    assert (error.code, error.message) == ("INVALID_PART_COUNT", "Choose Auto or 2 to 10 parts.")


@pytest.mark.parametrize("workflow", ["highlights", "topic", "recap", "trailer"])
def test_part_count_is_dropped_unvalidated_for_other_workflows(workflow):
    body = {"workflow": workflow, "workflow_options": {"part_count": "nonsense", "topic": "pricing"}}
    assert parse(body).options.part_count is None


def test_instructions_are_normalized_and_capped():
    raw = "  Focus on\tthe   hiring story.\r\n\r\n\r\n\r\nSkip the\x07 sponsor read.  \n  Keep it calm. "
    spec = parse({"workflow": "recap", "instructions": raw})
    assert spec.options.instructions == "Focus on the hiring story.\n\nSkip the sponsor read.\nKeep it calm."
    assert len(parse({"instructions": "x" * INSTRUCTIONS_MAX_CHARS}).options.instructions) == 500
    error = rejected({"instructions": "x" * 501})
    assert (error.code, error.message) == ("INSTRUCTIONS_TOO_LONG", "Keep instructions under 500 characters.")


def test_instructions_whitespace_does_not_count_toward_the_limit():
    spec = parse({"instructions": ("word" + " " * 10) * 90})
    assert len(spec.options.instructions) == 90 * 5 - 1


def test_top_level_instructions_win_over_workflow_options():
    body = {"workflow": "series", "instructions": "top", "workflow_options": {"instructions": "nested"}}
    assert parse(body).options.instructions == "top"
    body["instructions"] = "   "
    assert parse(body).options.instructions == "nested"
    del body["instructions"]
    assert parse(body).options.instructions == "nested"
    body["workflow_options"]["instructions"] = "y" * 501
    assert rejected(body).code == "INSTRUCTIONS_TOO_LONG"


def test_non_string_instructions_are_ignored():
    assert parse({"instructions": 12}).options.instructions == ""
    assert parse({"instructions": ["focus"]}).legacy is True


def test_not_available_is_a_403():
    error = workflow_not_available()
    assert (error.code, error.status) == ("WORKFLOW_NOT_AVAILABLE", 403)
    assert error.to_detail() == {
        "code": "WORKFLOW_NOT_AVAILABLE",
        "message": "This option isn't available on your account yet.",
    }


# --- text normalization -------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "keep", "expected"),
    [
        (None, True, ""),
        (42, False, ""),
        ("  a  b  ", False, "a b"),
        ("a\tb\x0bc", False, "a b c"),
        ("a\x00b\x1fc\x7f", False, "abc"),
        ("line\none", False, "line one"),
        ("line\none", True, "line\none"),
        ("a\r\nb\rc d", True, "a\nb\nc\nd"),
        ("a\n\n\n\nb", True, "a\n\nb"),
        ("a \n  b", True, "a\nb"),
        ("evil‮gnp.exe⁦", False, "evilgnp.exe"),
        ("café", False, "café"),
        ("نم‌ا", False, "نم‌ا"),
        ("\U0001f469‍\U0001f4bb", False, "\U0001f469‍\U0001f4bb"),
        ("a 　b", False, "a b"),
    ],
)
def test_normalize_text(raw, keep, expected):
    assert normalize_text(raw, keep_newlines=keep) == expected


# --- source duration (API) ------------------------------------------------------


def spec_for(workflow, clip_length="auto", part_count=None):
    return WorkflowSpec(workflow, WorkflowOptions(topic="x" if workflow == "topic" else "", part_count=part_count), clip_length)


@pytest.mark.parametrize(
    ("workflow", "minimum", "code", "message"),
    [
        ("series", 120, "SOURCE_TOO_SHORT_FOR_SERIES", "This video is too short to split into a series. Try Highlights instead."),
        ("recap", 180, "SOURCE_TOO_SHORT_FOR_RECAP", "This video is already short. Recaps work on videos over 3 minutes."),
        ("trailer", 180, "SOURCE_TOO_SHORT_FOR_TRAILER", "Trailers work on videos over 3 minutes."),
        ("topic", 60, "SOURCE_TOO_SHORT_FOR_TOPIC", "This video is too short to search by topic. Try Highlights instead."),
    ],
)
def test_source_minimums(workflow, minimum, code, message):
    check_source_duration(spec_for(workflow), minimum)
    check_source_duration(spec_for(workflow), None)
    with pytest.raises(WorkflowRequestError) as error:
        check_source_duration(spec_for(workflow), minimum - 0.5)
    assert (error.value.code, error.value.message, error.value.status) == (code, message, 422)
    assert error.value.to_detail()["min_source_seconds"] == minimum


def test_highlights_has_no_minimum():
    check_source_duration(spec_for("highlights"), 1)
    check_source_duration(WorkflowSpec("highlights", WorkflowOptions(instructions="x"), "auto"), 1)


@pytest.mark.parametrize(
    ("clip_length", "parts", "seconds", "fits"),
    [
        # auto parts need 45s * 1.25 = 56.25s of source each.
        ("auto", 4, 225, True),
        ("auto", 4, 224.9, False),
        ("short", 10, 375, True),
        ("short", 10, 374, False),
        ("long", 2, 225, True),
        ("medium", 3, 168.75, True),
    ],
)
def test_explicit_part_count_needs_room(clip_length, parts, seconds, fits):
    spec = spec_for("series", clip_length, parts)
    if fits:
        check_source_duration(spec, seconds)
        return
    with pytest.raises(WorkflowRequestError):
        check_source_duration(spec, seconds)


def test_too_many_parts_reports_the_largest_count_that_fits():
    with pytest.raises(WorkflowRequestError) as error:
        check_source_duration(spec_for("series", "auto", 8), 300)
    # floor(300 / 56.25) = 5
    assert error.value.code == "TOO_MANY_PARTS"
    assert error.value.message == "This video fits at most 5 parts. Choose fewer parts or Auto."
    assert error.value.to_detail() == {
        "code": "TOO_MANY_PARTS",
        "message": "This video fits at most 5 parts. Choose fewer parts or Auto.",
        "max_parts": 5,
    }


def test_too_many_parts_below_two_is_too_short_for_series():
    # Long parts need 112.5s each: a 200s video fits only one, so no series at all.
    with pytest.raises(WorkflowRequestError) as error:
        check_source_duration(spec_for("series", "long", 3), 200)
    assert error.value.code == "SOURCE_TOO_SHORT_FOR_SERIES"
    # The hint reports what two long parts need, not the 120s series minimum.
    assert error.value.to_detail()["min_source_seconds"] == 225
    check_source_duration(spec_for("series", "long", 2), 225)


def test_auto_part_count_only_needs_the_minimum():
    check_source_duration(spec_for("series", "long", None), 120)


# --- source duration (worker) ---------------------------------------------------


def test_runtime_minimum_errors_match_the_api_rule():
    assert workflow_runtime_minimum_error(spec_for("highlights"), 5) is None
    assert workflow_runtime_minimum_error(spec_for("recap"), 180) is None
    error = workflow_runtime_minimum_error(spec_for("trailer"), 179)
    assert isinstance(error, WorkflowError)
    assert (error.code, str(error)) == ("source_too_short", "Trailers work on videos over 3 minutes.")
    assert error.details == {"min_source_seconds": 180}
    # A part count that no longer fits is reduced by the planner, not failed.
    assert workflow_runtime_minimum_error(spec_for("series", "auto", 10), 200) is None


# --- errors ----------------------------------------------------------------------


def test_workflow_error_carries_code_message_and_details():
    error = WorkflowError("topic_not_found", "Nothing about pricing.", {"suggested_topics": ["hiring"]})
    assert isinstance(error, ValueError)
    assert (error.code, error.message, str(error)) == ("topic_not_found", "Nothing about pricing.", "Nothing about pricing.")
    assert error.details == {"suggested_topics": ["hiring"]}
    assert WorkflowError("plan_failed", "Try again.").details is None
    with pytest.raises(ValueError, match="Unknown workflow error code"):
        WorkflowError("made_up", "x")


def test_workflow_errors_survive_pickling():
    # ARQ pickles a failed job's exception into the job result.
    error = pickle.loads(pickle.dumps(WorkflowError("topic_not_found", "Nothing.", {"suggested_topics": ["a"]})))
    assert (type(error), error.code, str(error), error.details) == (
        WorkflowError, "topic_not_found", "Nothing.", {"suggested_topics": ["a"]},
    )
    request = pickle.loads(pickle.dumps(WorkflowRequestError("TOO_MANY_PARTS", "Fewer.", details={"max_parts": 3})))
    assert (request.status, request.to_detail()) == (422, {"max_parts": 3, "code": "TOO_MANY_PARTS", "message": "Fewer."})
    assert pickle.loads(pickle.dumps(workflow_not_available())).status == 403


# --- options JSON ------------------------------------------------------------------


def test_options_json_is_canonical_and_drops_empty_values():
    assert WorkflowOptions().to_json() == {}
    options = WorkflowOptions(topic="pricing", part_count=4, instructions="calm")
    assert list(options.to_json()) == ["instructions", "part_count", "topic"]
    assert options.to_json() == {"instructions": "calm", "part_count": 4, "topic": "pricing"}
    assert WorkflowOptions.from_json(options.to_json()) == options


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, WorkflowOptions()),
        ("not json", WorkflowOptions()),
        ([], WorkflowOptions()),
        (MagicMock(), WorkflowOptions()),
        ({"topic": 3, "part_count": "4", "instructions": None, "extra": True}, WorkflowOptions()),
        ({"part_count": True}, WorkflowOptions()),
        ({"part_count": 11}, WorkflowOptions()),
        ({"part_count": 3}, WorkflowOptions(part_count=3)),
        ('{"topic": "pricing"}', WorkflowOptions(topic="pricing")),
    ],
)
def test_options_from_json_is_lenient(raw, expected):
    assert WorkflowOptions.from_json(raw) == expected


# --- fingerprint -------------------------------------------------------------------


def test_fingerprint_is_stable_across_topic_spelling_and_key_order():
    one = parse({"workflow": "topic", "workflow_options": {"topic": "Building  a Business"}})
    two = parse({"workflow_options": {"topic": " building a\tbusiness "}, "workflow": "topic"})
    assert one.topic_key == two.topic_key == "building a business"
    assert one.fingerprint() == two.fingerprint()
    assert len(one.fingerprint()) == 64


def test_fingerprint_changes_with_every_input():
    base = WorkflowSpec("series", WorkflowOptions(part_count=4, instructions="calm"), "auto")
    variants = [
        WorkflowSpec("recap", WorkflowOptions(instructions="calm"), "auto"),
        WorkflowSpec("series", WorkflowOptions(part_count=5, instructions="calm"), "auto"),
        WorkflowSpec("series", WorkflowOptions(part_count=None, instructions="calm"), "auto"),
        WorkflowSpec("series", WorkflowOptions(part_count=4, instructions="Calm"), "auto"),
        WorkflowSpec("series", WorkflowOptions(part_count=4), "auto"),
        WorkflowSpec("series", WorkflowOptions(part_count=4, instructions="calm"), "long"),
    ]
    fingerprints = {spec.fingerprint() for spec in variants}
    assert base.fingerprint() not in fingerprints
    assert len(fingerprints) == len(variants)


def test_fingerprint_hashes_the_canonical_fields():
    spec = WorkflowSpec("topic", WorkflowOptions(topic="Pricing", instructions="calm"), "short")
    payload = {
        "clip_length": "short",
        "instructions": hashlib.sha256(b"calm").hexdigest()[:16],
        "part_count": None,
        "topic": "pricing",
        "version": WORKFLOW_PLAN_VERSION,
        "workflow": "topic",
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert spec.fingerprint() == hashlib.sha256(canonical.encode()).hexdigest()
    assert "calm" not in canonical
    assert WORKFLOW_PLAN_VERSION == "wf-v1" and OUTLINE_VERSION == "outline-v1"


# --- task rows ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "row",
    [None, MagicMock(), {}, {"workflow": None}, {"workflow": "bogus", "workflow_options": {"instructions": "x"}}, {"workflow": 3}],
)
def test_spec_from_unknown_rows_is_plain_highlights(row):
    spec = workflow_spec_from_task(row, "short")
    assert spec == WorkflowSpec("highlights", WorkflowOptions(), "short")
    assert spec.legacy is True


def test_spec_from_task_reads_the_stored_options():
    row = {"workflow": "series", "workflow_options": {"part_count": 4, "instructions": "calm", "topic": "stale"}}
    spec = workflow_spec_from_task(row, "medium")
    assert spec == WorkflowSpec("series", WorkflowOptions(part_count=4, instructions="calm"), "medium")
    highlights = workflow_spec_from_task({"workflow": "highlights", "workflow_options": {"instructions": "calm"}}, "auto")
    assert highlights.legacy is False
    assert workflow_spec_from_task({"workflow": "topic", "workflow_options": {"topic": "pricing"}}, "bogus").clip_length == "auto"


# --- plan cache ------------------------------------------------------------------------


def result(workflow="series"):
    return WorkflowResult(
        segments=[{"start_time": "00:01", "end_time": "01:00", "source_ranges": [[1.0, 60.0]], "planned": True}],
        plan={"version": WORKFLOW_PLAN_VERSION, "workflow": workflow, "title": "A story"},
        summary="Summary",
        key_topics=["money"],
        units_fingerprint="units-1",
    )


def test_result_cache_round_trip():
    cached = result().to_cache_json()
    restored = WorkflowResult.from_cache_json(cached, "series", "units-1")
    assert restored == result()


@pytest.mark.parametrize(
    ("raw", "workflow", "fingerprint"),
    [
        (None, "series", "units-1"),
        ("", "series", "units-1"),
        ("{broken", "series", "units-1"),
        ("[]", "series", "units-1"),
        ("cached", "recap", "units-1"),
        ("cached", "series", "units-2"),
        ("stale-version", "series", "units-1"),
        ("no-segments", "series", "units-1"),
        ("bad-topics", "series", "units-1"),
        # A highlights analysis stored under the same key shape.
        ('{"segments": [], "summary": "x", "key_topics": []}', "series", "units-1"),
    ],
)
def test_result_cache_misses(raw, workflow, fingerprint):
    payload = json.loads(result().to_cache_json())
    if raw == "cached":
        raw = result().to_cache_json()
    elif raw == "stale-version":
        raw = json.dumps({**payload, "version": "wf-v0"})
    elif raw == "no-segments":
        raw = json.dumps({**payload, "segments": []})
    elif raw == "bad-topics":
        raw = json.dumps({**payload, "key_topics": [1]})
    assert WorkflowResult.from_cache_json(raw, workflow, fingerprint) is None


# --- lengths -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("clip_length", "expected"),
    [
        ("auto", (45, 60, 100, 150, 155)),
        ("short", (30, 35, 50, 60, 65)),
        ("medium", (45, 55, 80, 90, 95)),
        ("long", (90, 100, 150, 175, 180)),
    ],
)
def test_series_part_budget(clip_length, expected):
    budget = durations.part_budget(clip_length)
    assert (budget.min, budget.ideal_min, budget.ideal_max, budget.max, budget.render_cap) == expected
    assert budget.maximum == budget.max and budget.minimum == budget.min
    assert budget.ideal == (expected[1] + expected[2]) / 2
    assert budget.render_cap <= durations.WORKFLOW_RENDER_HARD_MAX_SECONDS


@pytest.mark.parametrize(
    ("clip_length", "source", "target", "shortened"),
    [
        ("auto", 600, 45, False),
        ("auto", 601, 60, False),
        ("auto", 1800, 60, False),
        ("auto", 5400, 90, False),
        ("auto", 5401, 120, False),
        ("short", 3600, 45, False),
        ("medium", 3600, 75, False),
        ("long", 3600, 120, False),
        # T > 0.6 * D: shorten to max(30, round(0.4 * D)).
        ("long", 180, 72, True),
        ("medium", 120, 48, True),
        ("short", 70, 30, True),
    ],
)
def test_recap_budget(clip_length, source, target, shortened):
    budget = durations.recap_budget(clip_length, source)
    assert (budget.target, budget.shortened, budget.ideal) == (target, shortened, target)
    assert budget.min == round(0.85 * target, 1)
    assert budget.max == round(1.15 * target, 1)
    assert budget.render_cap == round(min(180, 1.15 * target + 5), 1)
    assert budget.min <= budget.ideal_min <= target <= budget.ideal_max <= budget.max


@pytest.mark.parametrize(("clip_length", "target"), [("auto", 30), ("short", 20), ("medium", 40), ("long", 60)])
def test_trailer_budget(clip_length, target):
    budget = durations.trailer_budget(clip_length)
    assert (budget.target, budget.min, budget.max) == (target, round(0.8 * target, 1), round(1.1 * target, 1))
    assert budget.render_cap == round(1.1 * target + 5, 1)
    assert budget.contains(target) and not budget.contains(budget.max + 0.1)


def test_topic_and_highlights_use_the_clip_presets():
    budget = durations.workflow_budget("topic", "long")
    assert (budget.min, budget.ideal_min, budget.ideal_max, budget.max, budget.render_cap) == (60, 75, 110, 120, 125)
    assert durations.workflow_budget("highlights", "auto") == durations.clip_budget("auto")
    assert durations.workflow_budget("series", "short") == durations.part_budget("short")
    with pytest.raises(ValueError):
        durations.workflow_budget("bogus", "auto")
    with pytest.raises(ValueError):
        durations.part_budget("custom")


def test_series_part_limits():
    assert (durations.SERIES_MIN_PARTS, durations.SERIES_MAX_PARTS) == (2, 10)
    assert durations.max_series_parts(300, "auto") == 5
    assert durations.max_series_parts(100000, "short") == 10
    assert durations.max_series_parts(100, "long") == 0


# --- GET /tasks/workflows ------------------------------------------------------------------


def test_workflows_info_matches_the_contract():
    assert workflows_info(True) == {
        "enabled": True,
        "workflows": [
            {"id": "highlights", "available": True, "min_source_seconds": 0},
            {
                "id": "series",
                "available": True,
                "min_source_seconds": 120,
                "part_count": {"min": 2, "max": 10},
                "lengths": {"auto": [45, 150], "short": [30, 60], "medium": [45, 90], "long": [90, 175]},
            },
            {"id": "topic", "available": True, "min_source_seconds": 60},
            {
                "id": "recap",
                "available": True,
                "min_source_seconds": 180,
                "lengths": {"auto": None, "short": 45, "medium": 75, "long": 120},
            },
            {
                "id": "trailer",
                "available": True,
                "min_source_seconds": 180,
                "lengths": {"auto": 30, "short": 20, "medium": 40, "long": 60},
            },
        ],
        "topic_max_chars": 120,
        "instructions_max_chars": 500,
    }
    json.dumps(workflows_info(True))


def test_disabled_workflows_info_only_offers_highlights():
    info = workflows_info(False)
    assert info["enabled"] is False
    assert {item["id"]: item["available"] for item in info["workflows"]} == {
        "highlights": True, "series": False, "topic": False, "recap": False, "trailer": False,
    }


# --- submission logs -----------------------------------------------------------------------


def test_submission_logs_record_the_workflow_id_only():
    from src.source_submission import SourceAttempt, record_fields, workflow_label

    assert [workflow_label(value) for value in (None, "series", "topic", "Series", "about pricing", 3)] == [
        "highlights", "series", "topic", "invalid", "invalid", "invalid",
    ]
    fields = record_fields(
        raw_url="https://www.youtube.com/watch?v=abcdefghijk",
        user_id="user-1",
        user_agent=None,
        outcome="rejected",
        status_code=422,
        detail={"code": "TOPIC_TOO_LONG", "message": "Keep the topic under 120 characters."},
        workflow="topic",
    )
    assert fields["workflow"] == "topic"
    assert record_fields(raw_url="upload://a.mp4", user_id=None, user_agent=None, outcome="accepted",
                         status_code=200)["workflow"] == "highlights"
    attempt = SourceAttempt(None, None)
    attempt.note_workflow("my secret topic")
    assert workflow_label(attempt.workflow) == "invalid"
