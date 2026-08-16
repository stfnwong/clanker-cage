"""Unit tests for clanker.session — durable, resumable sessions."""
import json

import pytest

from clanker.session import Session


# ─── create ───────────────────────────────────────────────────
def test_create_makes_dir_and_metadata(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    s = Session.create(tmp_path / "sessions", project)
    assert s.session_dir.is_dir()
    assert s.metadata_path.is_file()
    assert s.metadata["status"] == "active"
    assert s.metadata["project_root"] == str(project.resolve())
    assert s.metadata["project_name"] == "proj"


def test_create_generates_unique_ids(tmp_path):
    ids = {Session.create(tmp_path, tmp_path).session_id for _ in range(20)}
    assert len(ids) == 20  # all unique


def test_create_id_has_expected_shape(tmp_path):
    s = Session.create(tmp_path, tmp_path)
    parts = s.session_id.split("-")
    assert len(parts) == 3
    assert len(parts[2]) == 4  # short uuid suffix


def test_create_writes_metadata_to_disk(tmp_path):
    s = Session.create(tmp_path, tmp_path, model="m", provider="p")
    with s.metadata_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["model"] == "m"
    assert data["provider"] == "p"


def test_create_extra_kwargs_are_stored(tmp_path):
    s = Session.create(tmp_path, tmp_path, foo="bar", status="ignored")
    # 'status' is owned by Session; extra keys are added
    assert s.metadata["foo"] == "bar"
    assert s.metadata["status"] == "active"


def test_create_saves_then_reload(tmp_path):
    s = Session.create(tmp_path, tmp_path, model="m")
    s2 = Session(s.session_dir)
    assert s2.metadata["model"] == "m"


# ─── load ─────────────────────────────────────────────────────
def test_from_id_loads_existing(tmp_path):
    s = Session.create(tmp_path, tmp_path)
    loaded = Session.from_id(tmp_path, s.session_id)
    assert loaded.session_id == s.session_id


def test_from_id_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        Session.from_id(tmp_path, "does-not-exist")


# ─── transcript ───────────────────────────────────────────────
def test_append_and_read_events(tmp_path):
    s = Session.create(tmp_path, tmp_path)
    s.append({"role": "user", "content": "hi"})
    events = s.read_events()
    assert len(events) == 1
    assert events[0]["role"] == "user"
    assert "timestamp" in events[0]  # auto-added


def test_append_preserves_existing_timestamp(tmp_path):
    s = Session.create(tmp_path, tmp_path)
    s.append({"role": "user", "content": "x", "timestamp": "kept"})
    assert s.read_events()[0]["timestamp"] == "kept"


def test_read_events_empty_when_no_transcript(tmp_path):
    s = Session.create(tmp_path, tmp_path)
    assert s.read_events() == []


def test_iter_events_skips_malformed_lines(tmp_path):
    s = Session.create(tmp_path, tmp_path)
    s.append({"role": "user", "content": "ok"})
    s.transcript_path.open("a", encoding="utf-8").write("not-json\n")
    s.append({"role": "assistant", "content": "two"})
    events = list(s.iter_events())
    assert [e["role"] for e in events] == ["user", "assistant"]


# ─── status ───────────────────────────────────────────────────
def test_mark_finished_sets_metadata(tmp_path):
    s = Session.create(tmp_path, tmp_path)
    s.mark_finished(0)
    assert s.metadata["status"] == "finished"
    assert s.metadata["exit_code"] == 0
    assert "ended_at" in s.metadata


def test_mark_failed_sets_error(tmp_path):
    s = Session.create(tmp_path, tmp_path)
    s.mark_failed("boom")
    assert s.metadata["status"] == "failed"
    assert s.metadata["error"] == "boom"


# ─── render_text ──────────────────────────────────────────────
def test_render_text_formats_roles(tmp_path):
    s = Session.create(tmp_path, tmp_path)
    s.append({"role": "user", "content": "hello"})
    text = s.render_text()
    assert "USER: hello" in text


def test_render_text_truncates_long_content(tmp_path):
    s = Session.create(tmp_path, tmp_path)
    s.append({"role": "assistant", "content": "x" * 5000})
    text = s.render_text()
    assert "... [truncated]" in text
    assert len("x" * 5000) not in text  # assert marker-based truncation occurred


def test_render_text_includes_tool_calls(tmp_path):
    s = Session.create(tmp_path, tmp_path)
    s.append({
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "list_files", "arguments": "{}"}}],
    })
    text = s.render_text()
    assert "[tool_call] list_files({})" in text
