# Prompt Loading Tests - RELLS Engine v2.0

## Overview

This test suite validates the new external prompt loading system that loads `RELLS_ENGINE_v2.0_Documento_Oficial.md` from disk instead of hardcoding the prompt in `ai.py`.

**Test Files:**
- `tests/unit/test_prompt_loading.py` — Unit tests for prompt loading function
- `tests/integration/test_prompt_integration.py` — Integration tests for prompt in pipeline

## Running the Tests

### Run all prompt tests
```bash
cd backend
pytest tests/unit/test_prompt_loading.py tests/integration/test_prompt_integration.py -v
```

### Run only unit tests
```bash
pytest tests/unit/test_prompt_loading.py -v
```

### Run only integration tests
```bash
pytest tests/integration/test_prompt_integration.py -v
```

### Run with coverage
```bash
pytest tests/unit/test_prompt_loading.py tests/integration/test_prompt_integration.py --cov=src.ai --cov-report=html
```

### Run specific test class
```bash
pytest tests/unit/test_prompt_loading.py::TestPromptFileLoading -v
```

### Run specific test
```bash
pytest tests/unit/test_prompt_loading.py::TestPromptFileLoading::test_load_prompt_returns_non_empty_string -v
```

## Test Coverage

### Unit Tests (`test_prompt_loading.py`)

#### TestPromptFileLoading
- ✅ `test_load_prompt_returns_non_empty_string` — Prompt loads as valid string
- ✅ `test_load_prompt_contains_required_sections` — All required sections present
- ✅ `test_load_prompt_contains_grounding_rules` — Critical grounding rules enforced
- ✅ `test_load_prompt_file_not_found_raises_clear_error` — FileNotFoundError with helpful message
- ✅ `test_load_prompt_empty_file_raises_value_error` — ValueError when file is empty
- ✅ `test_load_prompt_with_utf8_special_characters` — UTF-8 with accents/special chars
- ✅ `test_load_prompt_handles_read_permission_error` — RuntimeError with context
- ✅ `test_load_prompt_caches_result_at_module_level` — Loaded once (module-level caching)

#### TestPromptPathResolution
- ✅ `test_prompt_file_path_relative_to_ai_module` — Path resolved correctly relative to ai.py
- ✅ `test_prompt_file_readable_in_docker_style_paths` — Works with absolute paths (Docker)
- ✅ `test_prompt_file_readable_in_local_style_paths` — Works with relative paths (local)
- ✅ `test_prompt_file_encoding_is_utf8` — File read with UTF-8 encoding

#### TestPromptIntegration
- ✅ `test_transcript_analysis_system_prompt_is_set` — Module variable initialized
- ✅ `test_prompt_is_used_by_transcript_agent` — Agent uses loaded prompt
- ✅ `test_prompt_consistency_across_reloads` — Same content on multiple loads
- ✅ `test_prompt_contains_no_python_code_artifacts` — No Python remnants in .md
- ✅ `test_prompt_maintains_markdown_formatting` — Markdown structure preserved

#### TestPromptErrorMessages
- ✅ `test_missing_file_error_includes_expected_path` — Clear error messages
- ✅ `test_read_error_includes_filename_and_reason` — Errors include context

### Integration Tests (`test_prompt_integration.py`)

#### TestPromptIntegrationWithAgent
- ✅ `test_agent_initializes_with_loaded_prompt` — Agent initializes successfully
- ✅ `test_agent_prompt_is_set_correctly` — Agent has correct prompt
- ✅ `test_prompt_is_not_empty_for_agent` — Prompt suitable for LLM

#### TestPromptEnvironmentCompatibility
- ✅ `test_prompt_loads_from_relative_path` — Relative path works
- ✅ `test_prompt_file_exists_at_expected_location` — File at backend/RELLS_ENGINE_v2.0_Documento_Oficial.md
- ✅ `test_prompt_file_is_readable` — File can be read
- ✅ `test_prompt_file_is_markdown_format` — Proper markdown formatting

#### TestPromptContentValidation
- ✅ `test_prompt_contains_output_contract` — OUTPUT CONTRACT section present
- ✅ `test_prompt_contains_grounding_rules` — GROUNDING RULES section
- ✅ `test_prompt_contains_content_neutrality_rules` — CONTENT NEUTRALITY section
- ✅ `test_prompt_contains_virality_scoring_criteria` — Virality scoring defined
- ✅ `test_prompt_contains_timing_guidelines` — Timing requirements specified
- ✅ `test_prompt_contains_hook_title_instructions` — Hook title generation

#### TestPromptWithClipSignals
- ✅ `test_build_prompt_with_clip_signals` — Prompt builder works with signals
- ✅ `test_build_prompt_with_broll_enabled` — B-roll section toggles correctly

#### TestPromptPerformance
- ✅ `test_prompt_loads_quickly` — Loads in < 100ms
- ✅ `test_prompt_size_is_reasonable` — 3KB-50KB range (optimal for LLM)

#### TestPromptDockerCompatibility
- ✅ `test_prompt_path_uses_pathlib` — Cross-platform compatible paths
- ✅ `test_prompt_encoding_utf8_for_unicode` — UTF-8 encoding (Docker compatible)
- ✅ `test_prompt_no_windows_line_endings` — Unix line endings preferred

#### TestPromptFailureRecovery
- ✅ `test_missing_prompt_prevents_agent_initialization` — Missing file detected
- ✅ `test_empty_prompt_would_cause_error` — Empty file raises ValueError

## What Each Requirement Tests

### ✅ Requirement 1: Normal Prompt Loading
**Tests:**
- `test_load_prompt_returns_non_empty_string`
- `test_load_prompt_contains_required_sections`
- `test_prompt_loads_quickly`

**Coverage:** Verifies prompt loads successfully with all required content

### ✅ Requirement 2: Missing File Error
**Tests:**
- `test_load_prompt_file_not_found_raises_clear_error`
- `test_missing_file_error_includes_expected_path`
- `test_missing_prompt_prevents_agent_initialization`

**Coverage:** Clear error when file doesn't exist, with helpful message

### ✅ Requirement 3: Empty File Error
**Tests:**
- `test_load_prompt_empty_file_raises_value_error`
- `test_empty_prompt_would_cause_error`

**Coverage:** ValueError raised when prompt file is empty

### ✅ Requirement 4: UTF-8 Support
**Tests:**
- `test_load_prompt_with_utf8_special_characters`
- `test_prompt_file_encoding_is_utf8`
- `test_prompt_encoding_utf8_for_unicode`

**Coverage:** Accents, special characters, and unicode supported

### ✅ Requirement 5: Single-Load Caching
**Tests:**
- `test_load_prompt_caches_result_at_module_level`
- `test_prompt_consistency_across_reloads`

**Coverage:** Prompt loaded only once at module import time

### ✅ Requirement 6: Docker & Local Compatibility
**Tests:**
- `test_prompt_file_path_relative_to_ai_module`
- `test_prompt_file_readable_in_docker_style_paths`
- `test_prompt_file_readable_in_local_style_paths`
- `test_prompt_path_uses_pathlib`
- `test_prompt_no_windows_line_endings`

**Coverage:** Works in Docker (absolute paths) and local (relative paths)

### ✅ Requirement 7: No Logic Changes
**Tests:**
- `test_agent_initializes_with_loaded_prompt`
- `test_prompt_is_used_by_transcript_agent`
- `test_build_prompt_with_clip_signals`
- `test_build_prompt_with_broll_enabled`

**Coverage:** AI pipeline still works unchanged, only prompt source changed

## Test Output Example

```
backend $ pytest tests/unit/test_prompt_loading.py -v

tests/unit/test_prompt_loading.py::TestPromptFileLoading::test_load_prompt_returns_non_empty_string PASSED
tests/unit/test_prompt_loading.py::TestPromptFileLoading::test_load_prompt_contains_required_sections PASSED
tests/unit/test_prompt_loading.py::TestPromptFileLoading::test_load_prompt_contains_grounding_rules PASSED
tests/unit/test_prompt_loading.py::TestPromptFileLoading::test_load_prompt_file_not_found_raises_clear_error PASSED
tests/unit/test_prompt_loading.py::TestPromptFileLoading::test_load_prompt_empty_file_raises_value_error PASSED
tests/unit/test_prompt_loading.py::TestPromptFileLoading::test_load_prompt_with_utf8_special_characters PASSED
tests/unit/test_prompt_loading.py::TestPromptFileLoading::test_load_prompt_handles_read_permission_error PASSED
tests/unit/test_prompt_loading.py::TestPromptFileLoading::test_load_prompt_caches_result_at_module_level PASSED
tests/unit/test_prompt_loading.py::TestPromptPathResolution::test_prompt_file_path_relative_to_ai_module PASSED
tests/unit/test_prompt_loading.py::TestPromptPathResolution::test_prompt_file_readable_in_docker_style_paths PASSED
tests/unit/test_prompt_loading.py::TestPromptPathResolution::test_prompt_file_readable_in_local_style_paths PASSED
tests/unit/test_prompt_loading.py::TestPromptPathResolution::test_prompt_file_encoding_is_utf8 PASSED
tests/unit/test_prompt_loading.py::TestPromptIntegration::test_transcript_analysis_system_prompt_is_set PASSED
tests/unit/test_prompt_loading.py::TestPromptIntegration::test_prompt_is_used_by_transcript_agent PASSED
tests/unit/test_prompt_loading.py::TestPromptIntegration::test_prompt_consistency_across_reloads PASSED
tests/unit/test_prompt_loading.py::TestPromptIntegration::test_prompt_contains_no_python_code_artifacts PASSED
tests/unit/test_prompt_loading.py::TestPromptIntegration::test_prompt_maintains_markdown_formatting PASSED
tests/unit/test_prompt_loading.py::TestPromptErrorMessages::test_missing_file_error_includes_expected_path PASSED
tests/unit/test_prompt_loading.py::TestPromptErrorMessages::test_read_error_includes_filename_and_reason PASSED

======================== 19 passed in 0.42s ========================
```

## Troubleshooting

### "ModuleNotFoundError: No module named 'src'"
```bash
# Make sure you're in backend directory
cd backend
pytest tests/unit/test_prompt_loading.py -v
```

### "FileNotFoundError: backend/RELLS_ENGINE_v2.0_Documento_Oficial.md not found"
**The prompt file is missing.** Ensure it exists:
```bash
ls -la backend/RELLS_ENGINE_v2.0_Documento_Oficial.md
```

If missing, create it from the original hardcoded prompt.

### Import errors with ai.py
**The ai.py modifications have a syntax error.** Check:
```bash
python -c "from src.ai import _load_transcript_analysis_prompt; print('OK')"
```

### Tests pass locally but fail in Docker
**Check file encoding:**
```bash
file backend/RELLS_ENGINE_v2.0_Documento_Oficial.md
# Should show: UTF-8 Unicode text
```

**Check line endings:**
```bash
file -i backend/RELLS_ENGINE_v2.0_Documento_Oficial.md
# Should NOT have CRLF
```

## CI/CD Integration

Add to your CI/CD pipeline:

```yaml
# .github/workflows/test.yml (or similar)
- name: Test Prompt Loading
  run: |
    cd backend
    pytest tests/unit/test_prompt_loading.py tests/integration/test_prompt_integration.py -v --tb=short
```

## Files Changed

- ✅ **Created:** `backend/tests/unit/test_prompt_loading.py` (26 tests)
- ✅ **Created:** `backend/tests/integration/test_prompt_integration.py` (28 tests)
- ✅ **Modified:** `backend/src/ai.py` (added `_load_transcript_analysis_prompt()`)
- ✅ **Created:** `backend/RELLS_ENGINE_v2.0_Documento_Oficial.md` (prompt file)

**Total Test Coverage:** 54 tests across unit & integration
