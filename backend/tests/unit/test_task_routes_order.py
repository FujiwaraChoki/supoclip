"""Static task routes must be declared before ``/{task_id}``, or a GET captures them as a task id (AI-M8)."""

from fastapi.routing import APIRoute

from src.api.routes import tasks


def _get_paths() -> list[str]:
    return [
        route.path
        for route in tasks.router.routes
        if isinstance(route, APIRoute) and "GET" in route.methods
    ]


def test_edit_styles_is_declared_before_the_task_id_route():
    paths = _get_paths()
    assert "/tasks/edit-styles" in paths
    assert paths.index("/tasks/edit-styles") < paths.index("/tasks/{task_id}")
    # Next to its sibling, as §5.2 says.
    assert paths.index("/tasks/workflows") < paths.index("/tasks/{task_id}")


def test_every_single_segment_static_get_route_precedes_the_task_id_route():
    paths = _get_paths()
    task_route = paths.index("/tasks/{task_id}")
    static = [
        path for path in paths
        if path.count("/") == 2 and "{" not in path and path != "/tasks/"
    ]
    assert static, "expected static routes such as /tasks/workflows"
    for path in static:
        assert paths.index(path) < task_route, f"{path} is shadowed by /tasks/{{task_id}}"


def test_the_edit_styles_request_reaches_its_own_handler():
    from starlette.routing import Match

    scope = {"type": "http", "path": "/tasks/edit-styles", "method": "GET", "root_path": ""}
    for route in tasks.router.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            assert route.endpoint is tasks.get_edit_styles
            break
    else:  # pragma: no cover - the route is missing entirely
        raise AssertionError("no route matched /tasks/edit-styles")


def test_clip_job_status_route_exists():
    assert "/tasks/{task_id}/clip-jobs/{job_id}" in _get_paths()
