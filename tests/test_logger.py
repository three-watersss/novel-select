import json
import os

from novel_selector.logger import cleanup_logs, configure_logging, workflow_event


def test_logging_redacts_secret_fields(tmp_path):
    configure_logging(tmp_path, level="INFO", max_file_bytes=1024 * 1024, backups=1, max_dir_bytes=1024 * 1024)
    workflow_event("secret_check", openai_api_key="sk-test", nested={"Authorization": "Bearer token"})

    line = (tmp_path / "workflow.log").read_text(encoding="utf-8").strip()
    payload = json.loads(line[line.find("{") :])
    assert payload["openai_api_key"] == "[REDACTED]"
    assert payload["nested"]["Authorization"] == "[REDACTED]"
    assert "sk-test" not in line
    assert "Bearer token" not in line


def test_cleanup_logs_deletes_oldest_files_first(tmp_path):
    old = tmp_path / "workflow.log.2"
    new = tmp_path / "workflow.log.1"
    old.write_text("a" * 80, encoding="utf-8")
    new.write_text("b" * 80, encoding="utf-8")
    old_mtime = 1_700_000_000
    new_mtime = old_mtime + 100
    os.utime(old, (old_mtime, old_mtime))
    os.utime(new, (new_mtime, new_mtime))

    cleanup_logs(tmp_path, max_dir_bytes=100)

    assert not old.exists()
    assert new.exists()
