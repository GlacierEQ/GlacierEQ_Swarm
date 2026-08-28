from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from swarm_api import Handler, SwarmHTTPServer
from swarm_runtime import ConfiguredWorker, SwarmRuntime
from swarm_store import SwarmStateStore
from worker_adapters import SubprocessWorkerAdapter


def worker_script(tmp_path: Path, name: str, *, succeed: bool = True) -> Path:
    path = tmp_path / name
    if succeed:
        body = (
            "import json,sys\n"
            "payload=json.load(sys.stdin)\n"
            "print(json.dumps({'worker':'%s','echo':payload,'ok':True}, sort_keys=True))\n"
            % name
        )
    else:
        body = (
            "import json,sys\n"
            "payload=json.load(sys.stdin)\n"
            "print('worker failed', file=sys.stderr)\n"
            "raise SystemExit(7)\n"
        )
    path.write_text(body, encoding="utf-8")
    return path


def configured(
    tmp_path: Path,
    worker_id: str,
    script: Path,
    *,
    caps=("python",),
    max_concurrency: int = 1,
):
    adapter = SubprocessWorkerAdapter(
        worker_id=worker_id,
        argv=[sys.executable, str(script)],
        cwd=tmp_path,
        timeout_seconds=30,
    )
    return ConfiguredWorker(
        worker_id=worker_id,
        capabilities=tuple(caps),
        max_concurrency=max_concurrency,
        adapter=adapter,
    )


def test_queued_task_survives_runtime_restart_and_executes(tmp_path: Path) -> None:
    script = worker_script(tmp_path, "worker.py")
    store_path = tmp_path / "swarm.sqlite3"
    first = SwarmRuntime(
        SwarmStateStore(store_path), [configured(tmp_path, "worker", script)]
    )
    first.submit_task("task-1", ["python"], {"value": 42}, priority=5)
    before = first.status()
    assert before["task_status"]["QUEUED"] == 1

    second = SwarmRuntime(
        SwarmStateStore(store_path), [configured(tmp_path, "worker", script)]
    )
    result = second.run_until_idle()
    assert result["status"] == "IDLE"
    task = second.task("task-1")
    assert task["task"]["status"] == "SUCCEEDED"
    assert task["result"]["result"]["echo"] == {"value": 42}
    assert task["result"]["result"]["worker"] == "worker.py"
    assert second.store.integrity_report()["status"] == "PASS"


def test_failed_worker_is_retried_on_alternate_real_adapter(tmp_path: Path) -> None:
    failing = worker_script(tmp_path, "a_fail.py", succeed=False)
    succeeding = worker_script(tmp_path, "b_ok.py", succeed=True)
    runtime = SwarmRuntime(
        SwarmStateStore(tmp_path / "swarm.sqlite3"),
        [
            configured(tmp_path, "a", failing),
            configured(tmp_path, "b", succeeding),
        ],
    )
    runtime.submit_task("task", ["python"], {"objective": "recover"}, max_attempts=3)
    result = runtime.run_until_idle()
    assert result["status"] == "IDLE"
    task = runtime.task("task")
    assert task["task"]["status"] == "SUCCEEDED"
    assert task["task"]["attempts"] == 2
    assert "a" in task["task"]["failed_worker_ids"]
    assert task["result"]["result"]["worker"] == "b_ok.py"
    assert runtime.orchestrator.verify_telemetry()["status"] == "PASS"


def test_store_rejects_concurrent_stale_revision(tmp_path: Path) -> None:
    store = SwarmStateStore(tmp_path / "swarm.sqlite3")
    orchestrator, revision = store.load()
    orchestrator.register_agent("worker", ["python"])
    new_revision = store.save(orchestrator, expected_revision=revision)
    assert new_revision > revision
    other, _ = store.load()
    other.register_agent("second", ["python"])
    try:
        store.save(other, expected_revision=revision)
    except ValueError as exc:
        assert "swarm_revision_conflict" in str(exc)
    else:
        raise AssertionError("stale revision was accepted")


def test_adapter_refuses_non_json_success_output(tmp_path: Path) -> None:
    script = tmp_path / "bad.py"
    script.write_text("print('not json')\n", encoding="utf-8")
    adapter = SubprocessWorkerAdapter(
        worker_id="bad",
        argv=[sys.executable, str(script)],
        cwd=tmp_path,
        timeout_seconds=30,
    )
    receipt = adapter.execute({"x": 1})
    assert receipt.status == "FAIL"
    assert receipt.returncode == 126
    assert "worker_stdout_not_single_json_value" in receipt.stderr_tail


def _request(url: str, *, method: str = "GET", token: str | None = None, body=None):
    headers = {}
    data = None
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_http_api_requires_auth_and_executes_real_task(tmp_path: Path) -> None:
    script = worker_script(tmp_path, "api_worker.py")
    runtime = SwarmRuntime(
        SwarmStateStore(tmp_path / "swarm.sqlite3"),
        [configured(tmp_path, "api-worker", script, caps=("python", "tests"))],
    )
    token = "t" * 32
    server = SwarmHTTPServer(
        ("127.0.0.1", 0), Handler, runtime=runtime, bearer_token=token
    )
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, health = _request(base + "/health")
        assert status == 200
        assert health["status"] == "HEALTHY"
        status, readiness = _request(base + "/ready")
        assert status == 200
        assert readiness["ready"] is True

        try:
            _request(base + "/v1/status")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("unauthenticated status was accepted")

        status, submitted = _request(
            base + "/v1/tasks",
            method="POST",
            token=token,
            body={
                "task_id": "api-task",
                "required_capabilities": ["python", "tests"],
                "payload": {"from": "api"},
                "priority": 9,
            },
        )
        assert status == 201
        assert submitted["status"] == "QUEUED"

        status, executed = _request(
            base + "/v1/run-until-idle", method="POST", token=token, body={}
        )
        assert status == 200
        assert executed["status"] == "IDLE"

        status, task = _request(base + "/v1/tasks/api-task", token=token)
        assert status == 200
        assert task["task"]["status"] == "SUCCEEDED"
        assert task["result"]["result"]["echo"] == {"from": "api"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
