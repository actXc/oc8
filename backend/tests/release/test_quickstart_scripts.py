"""Static contracts for the public local-install quickstart scripts."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_posix_quickstart_creates_missing_secrets_without_resetting_data() -> None:
    script = (REPO_ROOT / "scripts" / "quickstart.sh").read_text(encoding="utf-8")

    assert "OC8_JWT_SECRET" in script
    assert "OC8_SECRET_KEK" in script
    assert "OC8_SANDBOX_PROVISIONER_TOKEN" in script
    assert "docker compose up -d --build" in script
    assert "down --volumes" not in script
    assert "rm -rf" not in script


def test_posix_quickstart_supports_host_port_compose_bindings() -> None:
    script = (REPO_ROOT / "scripts" / "quickstart.sh").read_text(encoding="utf-8")

    assert "port_mapping" in script
    assert "127.0.0.1" in script


def test_windows_quickstart_checks_docker_and_preserves_existing_env() -> None:
    script = (REPO_ROOT / "scripts" / "quickstart.ps1").read_text(encoding="utf-8")

    assert "docker compose version" in script
    assert "docker info" in script
    assert "Set-EnvValueIfMissing" in script
    assert "docker compose up -d --build" in script
    assert "down --volumes" not in script


def test_public_docs_link_to_the_quickstart_scripts() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    guide = (REPO_ROOT / "docs" / "GETTING_STARTED.md").read_text(encoding="utf-8")

    assert "scripts/quickstart.sh" in readme
    assert "scripts/quickstart.ps1" in readme
    assert "quickstart.sh" in guide
    assert "quickstart.ps1" in guide
