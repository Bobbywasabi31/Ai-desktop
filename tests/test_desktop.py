"""Tests for ai-desktop. Run with: python -m pytest"""

import os
import time

import pytest

from aidesktop import Desktop, SafetyError
from aidesktop import shell as shell_mod
from aidesktop import files as files_mod
from aidesktop import safety as safety_mod


@pytest.fixture()
def desk(tmp_path):
    d = Desktop(workdir=str(tmp_path / "work"))
    yield d
    d.close()


def test_shell_foreground(desk):
    r = desk.shell("echo hello")
    assert r.ok and r.stdout.strip() == "hello"


def test_shell_timeout():
    r = shell_mod.run("sleep 5", timeout=0.3)
    assert r.timed_out and r.exit_code is None


def test_shell_background_poll_kill(desk):
    job = desk.shell("echo start && sleep 30 && echo never", background=True)
    assert job.running()
    assert job.poll(timeout=5) is None  # still running
    assert "start" in job.log()
    result = job.kill()
    assert not job.running()
    assert "start" in result.stdout


def test_background_job_completes(desk):
    job = desk.shell("echo done", background=True)
    result = job.wait(timeout=10)
    assert result.ok and result.stdout.strip() == "done"


def test_files_roundtrip(desk):
    desk.write("a/b.txt", "hello")
    assert desk.read("a/b.txt") == "hello"
    assert desk.edit("a/b.txt", "hello", "hi") == 1
    assert desk.read("a/b.txt") == "hi"
    names = [e["name"] for e in desk.list("a")]
    assert "b.txt" in names


def test_files_sandbox_escape(desk):
    with pytest.raises(files_mod.PathEscapeError):
        desk.read("../../etc/passwd")
    with pytest.raises(files_mod.PathEscapeError):
        desk.write("../evil.txt", "x")


def test_blocklist_blocks_rm_rf_slash():
    with pytest.raises(SafetyError):
        safety_mod.check_command("rm -rf / --no-preserve-root")


def test_blocklist_blocks_fork_bomb():
    with pytest.raises(SafetyError):
        safety_mod.check_command(":(){ :|:& };:")


def test_blocklist_allows_normal_commands():
    safety_mod.check_command("ls -la /tmp")
    safety_mod.check_command("echo hello > out.txt")


def test_irreversible_shell_needs_approval(tmp_path):
    d = Desktop(workdir=str(tmp_path), approver=safety_mod.Approver(confirm=lambda desc: False))
    with pytest.raises(SafetyError):
        d.shell("rm -rf ./build")
    d.close()


def test_irreversible_shell_approved(tmp_path):
    d = Desktop(workdir=str(tmp_path), approver=safety_mod.Approver(confirm=lambda desc: True))
    d.write("build/x.txt", "x")
    r = d.shell("rm -rf ./build")
    assert r.ok and not d.exists("build")
    d.close()


def test_remove_needs_approval(desk):
    desk.write("gone.txt", "x")
    with pytest.raises(SafetyError):
        desk.remove("gone.txt")  # default approver denies


def test_merchant_style_edit_missing_text(desk):
    desk.write("f.txt", "aaa")
    with pytest.raises(ValueError):
        desk.edit("f.txt", "zzz", "b")


def test_jobs_tracked(desk):
    j1 = desk.shell("sleep 2", background=True)
    j2 = desk.shell("echo hi", background=True)
    assert len(desk.jobs()) == 2
    j1.kill()
    j2.wait(timeout=10)
