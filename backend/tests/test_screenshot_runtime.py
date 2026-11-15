import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

import pytest

from app import screenshot_ocr as ocr


def valid_payload():
    return {"rows": [{"nickname": "hamy", "kills": 22, "deaths": 7, "assists": 0, "team": "ct",
                      "game_score": 46, "rank": 1}],
            "mode": "competitive", "match_id": "2188356974", "score": [8, 6],
            "map_name": "Sandstone", "warnings": []}


def encoded(value=None):
    return json.dumps(valid_payload() if value is None else value, ensure_ascii=False).encode()


def test_compact_schema_preserves_kda_and_unknown_fields_are_not_saved():
    payload = valid_payload()
    payload["raw_text"] = "must not persist"
    payload["rows"][0]["raw_crop"] = "must not persist"
    result = ocr.parse_scoreboard(payload)
    assert result.rows[0].kda == (22, 7, 0)
    assert result.rows[0].game_score == 46 and result.rows[0].rank == 1
    assert result.score == (8, 6)
    assert result.map_name == "Sandstone"
    assert "raw_text" not in asdict(result) and "raw_crop" not in asdict(result.rows[0])
    payload["rows"][0].pop("assists")
    assert ocr.parse_scoreboard(payload).rows[0].assists is None
    assert ocr.parse_scoreboard(payload).rows[0].kda is None


def test_real_alternative_ocr_names_are_preserved_without_replacing_the_primary():
    payload = valid_payload()
    payload["rows"][0]["nickname"] = "harny"
    payload["rows"][0]["aliases"] = ["Багпу", "Багпу", "harny"]
    row = ocr.parse_scoreboard(payload).rows[0]
    assert row.nickname == "harny" and row.aliases == ["Багпу"]
    assert row.kda == (22, 7, 0)
    assert ocr.PlayerRow("no alternatives").aliases == []


@pytest.mark.parametrize("change", [
    lambda p: p["rows"][0].update(kills=True),
    lambda p: p["rows"][0].update(deaths=-1),
    lambda p: p["rows"][0].update(assists=201),
    lambda p: p["rows"][0].update(nickname="x" * 81),
    lambda p: p["rows"][0].update(nickname=" " * 81 + "x"),
    lambda p: p["rows"][0].update(nickname="hidden\nrow"),
    lambda p: p["rows"][0].update(team={"not": "a team"}),
    lambda p: p.update(rows=p["rows"] * 11),
    lambda p: p.update(mode="duels"),
    lambda p: p.update(mode=[]),
    lambda p: p.update(match_id=1234),
    lambda p: p.update(match_id="１２３"),
    lambda p: p.update(score=[8, None]),
    lambda p: p.update(score=[True, 6]),
    lambda p: p.update(warnings=["a" * 301]),
    lambda p: p.update(map_name="x" * 129),
    lambda p: p.update(map_name="hidden\nname"),
    lambda p: p.update(map_name=True),
    lambda p: p["rows"][0].update(aliases="not a list"),
    lambda p: p["rows"][0].update(aliases=["a", "b", "c", "d"]),
    lambda p: p["rows"][0].update(aliases=["a" * 81]),
    lambda p: p["rows"][0].update(aliases=[" " * 81 + "a"]),
    lambda p: p["rows"][0].update(aliases=[""]),
    lambda p: p["rows"][0].update(aliases=["hidden\nrow"]),
    lambda p: p["rows"][0].update(game_score=-1),
    lambda p: p["rows"][0].update(game_score=1000),
    lambda p: p["rows"][0].update(game_score=True),
    lambda p: p["rows"][0].update(rank=0),
    lambda p: p["rows"][0].update(rank=11),
    lambda p: p["rows"][0].update(rank=True),
])
def test_invalid_worker_schema_is_rejected(change):
    payload = valid_payload()
    change(payload)
    with pytest.raises(ocr.OCRException):
        ocr.parse_scoreboard(payload)


def test_worker_errors_and_bad_json_do_not_publish_stderr():
    with pytest.raises(ocr.OCRException, match="Не вижу итоговую таблицу"):
        ocr._decode_result(2, encoded({"error": "Не вижу итоговую таблицу."}))
    for status, output in [(0, b"not-json"), (0, b"\xff"), (1, encoded()), (0, b"x" * (ocr.MAX_STDOUT_BYTES + 1))]:
        with pytest.raises(ocr.OCRException) as error:
            ocr._decode_result(status, output)
        assert "not-json" not in str(error.value)


class FakeStdin:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, *, stdout=None, stderr=b"", status=0, hanging=False):
        self.stdin = FakeStdin()
        self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        if stdout is not None:
            self.stdout.feed_data(stdout)
            self.stdout.feed_eof()
        elif not hanging:
            self.stdout.feed_eof()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.pid, self.returncode, self.status = 876543, None, status
        self.done, self.killed = asyncio.Event(), False
        if not hanging:
            self.done.set()

    async def wait(self):
        await self.done.wait()
        self.returncode = self.status
        return self.status

    def kill(self):
        self.killed = True
        self.status = -9
        self.done.set()
        self.stdout.feed_eof()


def test_worker_uses_fixed_module_and_secret_free_environment(monkeypatch):
    async def scenario():
        process = FakeProcess(stdout=encoded())
        captured = {}
        async def spawn(*args, **kwargs):
            captured["args"], captured["kwargs"] = args, kwargs
            return process
        monkeypatch.setattr(ocr.asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setenv("BOT_TOKEN", "secret-test-token")
        monkeypatch.setenv("DATABASE_URL", "secret-test-db")
        monkeypatch.setenv("SOURCE_SESSION", "secret-test-session")
        monkeypatch.setenv("TESSERACT_CMD", "trusted-tesseract")
        status, output = await ocr._run_worker(b"image-bytes")
        assert status == 0 and output == encoded()
        assert captured["args"] == (sys.executable, "-m", "app.screenshot_ocr_worker")
        kwargs = captured["kwargs"]
        assert kwargs["limit"] == 128 * 1024 and kwargs["close_fds"]
        assert Path(kwargs["cwd"]).name == "backend"
        assert not {"BOT_TOKEN", "DATABASE_URL", "SOURCE_SESSION"} & kwargs["env"].keys()
        assert kwargs["env"]["TESSERACT_CMD"] == "trusted-tesseract"
        assert kwargs["env"]["OMP_THREAD_LIMIT"] == kwargs["env"]["OPENBLAS_NUM_THREADS"] == "1"
        scratch = Path(kwargs["env"]["TMPDIR"])
        assert kwargs["env"]["TMP"] == kwargs["env"]["TEMP"] == str(scratch)
        assert not scratch.exists()
        assert process.stdin.data == b"image-bytes" and process.stdin.closed
    asyncio.run(scenario())


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_excess_output_kills_worker_and_is_never_returned(monkeypatch, stream):
    async def scenario():
        limit = ocr.MAX_STDOUT_BYTES if stream == "stdout" else ocr.MAX_STDERR_BYTES
        process = FakeProcess(stdout=b"x" * (limit + 1) if stream == "stdout" else encoded(),
                              stderr=b"secret-error" * (limit + 1) if stream == "stderr" else b"")
        killed = []
        async def spawn(*args, **kwargs):
            return process
        async def terminate(target):
            killed.append(target)
            target.kill()
        monkeypatch.setattr(ocr.asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(ocr, "_terminate_worker", terminate)
        service = ocr.OCRService()
        with pytest.raises(ocr.OCRException) as error:
            await service.analyze(b"image")
        assert killed == [process]
        assert "secret-error" not in str(error.value)
        assert service._admitted == 0
    asyncio.run(scenario())


def test_timeout_and_cancellation_kill_worker_and_release_admission(monkeypatch):
    async def scenario():
        processes, killed = [], []
        async def spawn(*args, **kwargs):
            process = FakeProcess(hanging=True)
            processes.append(process)
            return process
        async def terminate(process):
            killed.append(process)
            process.kill()
        monkeypatch.setattr(ocr.asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(ocr, "_terminate_worker", terminate)
        service = ocr.OCRService(timeout=0.02)
        with pytest.raises(ocr.OCRException, match="Не успел"):
            await service.analyze(b"image")
        assert service._admitted == 0 and killed == processes
        service = ocr.OCRService(timeout=20)
        task = asyncio.create_task(service.analyze(b"image"))
        while len(processes) < 2:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert killed == processes and service._admitted == 0
    asyncio.run(scenario())


def test_only_one_worker_and_one_pending_request_are_admitted():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls, active, peak = 0, 0, 0
        async def runner(payload):
            nonlocal calls, active, peak
            calls += 1
            active += 1
            peak = max(peak, active)
            entered.set()
            try:
                await release.wait()
                return 0, encoded()
            finally:
                active -= 1
        service = ocr.OCRService(runner=runner)
        first = asyncio.create_task(service.analyze(b"first"))
        await entered.wait()
        pending = asyncio.create_task(service.analyze(b"second"))
        await asyncio.sleep(0)
        assert service._admitted == 2
        with pytest.raises(ocr.OCRException, match="занято"):
            await service.analyze(b"third")
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        replacement = asyncio.create_task(service.analyze(b"replacement"))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, replacement)
        assert calls == 2 and peak == 1 and service._admitted == 0
    asyncio.run(scenario())


def test_bad_or_oversized_input_never_starts_worker():
    async def scenario():
        async def runner(_):
            raise AssertionError("must not execute")
        service = ocr.OCRService(runner=runner)
        for payload in (b"", "not bytes", b"x" * (ocr.MAX_UPLOAD_BYTES + 1)):
            with pytest.raises(ocr.OCRException):
                await service.analyze(payload)
        assert service._admitted == 0
    asyncio.run(scenario())


def test_posix_cleanup_targets_owned_group_even_if_leader_has_exited(monkeypatch):
    async def scenario():
        process = FakeProcess()
        process.returncode = 0
        calls = []
        monkeypatch.setattr(ocr, "_POSIX", True)
        monkeypatch.setattr(ocr.os, "killpg", lambda pid, sig: calls.append((pid, sig)), raising=False)
        await ocr._terminate_worker(process)
        assert calls == [(process.pid, ocr._KILL_SIGNAL)]
    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["success", "error", "timeout", "cancel"])
def test_parent_removes_worker_scratch_even_when_worker_finally_does_not_run(monkeypatch, outcome):
    async def scenario():
        scratch_paths, processes = [], []
        started = asyncio.Event()
        async def spawn(*args, **kwargs):
            scratch = Path(kwargs["env"]["TMPDIR"])
            assert scratch.is_absolute() and scratch.is_dir()
            assert scratch.parent == Path(ocr.tempfile.gettempdir()).resolve()
            assert scratch.name.startswith("so2-scoreboard-")
            nested = scratch / "worker-private-crops"
            nested.mkdir()
            (nested / "cropped-screenshot.png").write_bytes(b"private-image-content")
            scratch_paths.append(scratch)
            process = FakeProcess(
                stdout=encoded() if outcome == "success" else None,
                hanging=outcome != "success",
            )
            if outcome == "error":
                process.stderr = asyncio.StreamReader()
                process.stderr.feed_data(b"x" * (ocr.MAX_STDERR_BYTES + 1))
                process.stderr.feed_eof()
            processes.append(process)
            started.set()
            return process
        async def terminate(process):
            process.kill()
        monkeypatch.setattr(ocr.asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(ocr, "_terminate_worker", terminate)
        service = ocr.OCRService(timeout=0.02 if outcome == "timeout" else 20)
        if outcome == "cancel":
            task = asyncio.create_task(service.analyze(b"image"))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif outcome in {"timeout", "error"}:
            with pytest.raises(ocr.OCRException):
                await service.analyze(b"image")
        else:
            assert (await service.analyze(b"image")).rows[0].nickname == "hamy"
        assert len(scratch_paths) == 1 and not scratch_paths[0].exists()
        assert service._admitted == 0
        if outcome != "success":
            assert processes[0].killed
    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group integration")
def test_real_timeout_stops_worker_and_its_descendant(monkeypatch, tmp_path):
    async def scenario():
        pid_file = tmp_path / "ocr-test-child.pid"
        child = ("import os,time; from pathlib import Path; "
                 f"Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)")
        parent = ("import os,subprocess,sys,time; from pathlib import Path; "
                  "(Path(os.environ['TMPDIR'])/'private-crop.png').write_bytes(b'private'); "
                  f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(60)")
        real_spawn = asyncio.create_subprocess_exec
        created, scratch_paths = [], []
        async def spawn(*args, **kwargs):
            scratch_paths.append(Path(kwargs["env"]["TMPDIR"]))
            process = await real_spawn(sys.executable, "-c", parent, **kwargs)
            created.append(process)
            return process
        monkeypatch.setattr(ocr.asyncio, "create_subprocess_exec", spawn)
        service = ocr.OCRService(timeout=1.0)
        with pytest.raises(ocr.OCRException, match="Не успел"):
            await service.analyze(b"image")
        assert created[0].returncode is not None and pid_file.exists()
        assert len(scratch_paths) == 1 and not scratch_paths[0].exists()
        pid = int(pid_file.read_text())
        for _ in range(20):
            stat_path = Path(f"/proc/{pid}/stat")
            if not stat_path.exists() or stat_path.read_text().split()[2] == "Z":
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("OCR descendant is still running")
    asyncio.run(scenario())
