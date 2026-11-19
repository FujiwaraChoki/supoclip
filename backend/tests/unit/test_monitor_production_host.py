import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "monitor-production.py"


def load_script():
    spec = importlib.util.spec_from_file_location("monitor_production", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_requires_ssh_host_from_environment(monkeypatch):
    monkeypatch.delenv("SUPOCLIP_PROD_SSH_HOST", raising=False)
    module = load_script()
    monkeypatch.setattr(module.subprocess, "run", pytest.fail)
    monkeypatch.setattr(module.urllib.request, "urlopen", pytest.fail)
    with pytest.raises(SystemExit, match="SUPOCLIP_PROD_SSH_HOST"):
        module.main()


def _offline(*args, **kwargs):
    raise OSError("offline")


def test_ssh_uses_configured_host(monkeypatch, capsys):
    monkeypatch.setenv("SUPOCLIP_PROD_SSH_HOST", "prod.example")
    module = load_script()
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout="{}")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.urllib.request, "urlopen", _offline)
    module.main()
    assert len(calls) == 2 and all(argv[0] == "ssh" and "prod.example" in argv for argv in calls)
    assert json.loads(capsys.readouterr().out)["production"] == {}
