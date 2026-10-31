import pytest
from tests.unit.test_workflow_boundaries import cached_video, test_adjacent_parts_share_one_exact_cut as inner, CLEANUP_ON  # noqa

@pytest.mark.parametrize("seed", list(range(1000, 1300)) + list(range(2000, 2400)) + list(range(3000, 3400)))
@pytest.mark.parametrize("cleanup", [None, CLEANUP_ON], ids=["off", "on"])
def test_sweep(seed, cleanup, cached_video):
    inner.__wrapped__(seed, cleanup, cached_video) if hasattr(inner, "__wrapped__") else inner(seed, cleanup, cached_video)
