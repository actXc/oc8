"""Tests for edition export tooling (scripts/export_edition.py).

These tests verify that export operations:
- Handle empty, dirty, and already-exported directories
- Produce correct file lists for each edition
- Enforce edition boundaries
- Validate forbidden imports
- Never modify the source monorepo
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

# Add scripts directory to path for import
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import export_edition


class TestExportConfigInitialization:
    """Test ExportConfig initialization and validation."""

    def test_community_config(self) -> None:
        """Test Community edition config initialization."""
        config = export_edition.ExportConfig(edition="community")
        assert config.edition == "community"
        assert config.dry_run is True
        assert config.output_dir is None
        assert "backend/src/oc8" in config.allowlist["dirs"]
        assert "frontend/src" in config.allowlist["dirs"]
        assert "enterprise" in config.allowlist["forbidden_dirs"]
        assert "saas" in config.allowlist["forbidden_dirs"]

    def test_enterprise_config(self) -> None:
        """Test Enterprise edition config initialization."""
        config = export_edition.ExportConfig(edition="enterprise")
        assert config.edition == "enterprise"
        assert "enterprise/backend/src/oc8_enterprise" in config.allowlist["dirs"]
        assert "saas" in config.allowlist["forbidden_dirs"]
        assert "oc8_cloud" in config.allowlist["forbidden_imports"]

    def test_saas_config(self) -> None:
        """Test SaaS edition config initialization."""
        config = export_edition.ExportConfig(edition="saas")
        assert config.edition == "saas"
        assert "saas/control-plane" in config.allowlist["dirs"]
        assert "saas/billing" in config.allowlist["dirs"]

    def test_invalid_edition(self) -> None:
        """Test that invalid edition raises ValueError."""
        with pytest.raises(ValueError, match="Unknown edition"):
            export_edition.ExportConfig(edition="invalid")

    def test_output_dir_parameter(self) -> None:
        """Test output directory parameter."""
        output = Path("/tmp/test-export")
        config = export_edition.ExportConfig(edition="community", output_dir=output)
        assert config.output_dir == output
        # When output_dir is provided, dry_run should still be True initially
        assert config.dry_run is True

    def test_overwrite_flag(self) -> None:
        """Test overwrite flag."""
        config = export_edition.ExportConfig(
            edition="community", overwrite=True
        )
        assert config.overwrite is True


class TestExportResultDataclass:
    """Test ExportResult dataclass."""

    def test_result_creation(self) -> None:
        """Test creating an ExportResult."""
        result = export_edition.ExportResult(
            edition="community",
            success=True,
            timestamp="2026-08-04T10:00:00+00:00",
            dry_run=True,
        )
        assert result.edition == "community"
        assert result.success is True
        assert result.dry_run is True
        assert result.files_counted == 0
        assert result.files_copied == 0

    def test_result_to_dict(self) -> None:
        """Test converting result to dictionary."""
        result = export_edition.ExportResult(
            edition="enterprise",
            success=False,
            timestamp="2026-08-04T10:00:00+00:00",
            dry_run=False,
            files_counted=100,
            files_copied=50,
        )
        result_dict = result.to_dict()
        assert isinstance(result_dict, dict)
        assert result_dict["edition"] == "enterprise"
        assert result_dict["success"] is False
        assert result_dict["files_counted"] == 100
        assert result_dict["files_copied"] == 50

    def test_result_with_violations(self) -> None:
        """Test result with violations and warnings."""
        result = export_edition.ExportResult(
            edition="community",
            success=False,
            timestamp="2026-08-04T10:00:00+00:00",
            dry_run=True,
            violations=["violation1", "violation2"],
            warnings=["warning1"],
        )
        assert len(result.violations) == 2
        assert len(result.warnings) == 1
        assert result.success is False


class TestImportValidation:
    """Test import validation functions."""

    def test_imported_roots_with_simple_imports(self, tmp_path: Path) -> None:
        """Test extracting imports from Python file."""
        py_file = tmp_path / "test.py"
        py_file.write_text(
            """
import os
import sys
from pathlib import Path
from typing import Any

import pytest
"""
        )
        imports = export_edition.imported_roots(py_file)
        roots = [root for _lineno, root in imports]
        assert "os" in roots
        assert "sys" in roots
        assert "pathlib" in roots
        assert "typing" in roots
        assert "pytest" in roots

    def test_imported_roots_with_package_imports(self, tmp_path: Path) -> None:
        """Test extracting package imports."""
        py_file = tmp_path / "test.py"
        py_file.write_text(
            """
from oc8.domain import something
import oc8_enterprise.features
from saas.billing import charge
"""
        )
        imports = export_edition.imported_roots(py_file)
        roots = [root for _lineno, root in imports]
        assert "oc8" in roots
        assert "oc8_enterprise" in roots
        assert "saas" in roots

    def test_imported_roots_syntax_error(self, tmp_path: Path) -> None:
        """Test handling of syntax errors."""
        py_file = tmp_path / "broken.py"
        py_file.write_text("this is not valid python ][{")
        imports = export_edition.imported_roots(py_file)
        assert imports == []

    def test_imported_roots_nonexistent_file(self, tmp_path: Path) -> None:
        """Test handling of nonexistent files."""
        py_file = tmp_path / "nonexistent.py"
        imports = export_edition.imported_roots(py_file)
        assert imports == []


class TestPathInclusionLogic:
    """Test path inclusion and exclusion logic."""

    def test_should_include_path_exact_match(self) -> None:
        """Test exact path match."""
        # Assuming we're in repo root
        path = REPO_ROOT / "backend" / "pyproject.toml"
        allowlist = ["backend/pyproject.toml", "frontend/src/**"]
        result = export_edition.should_include_path(path, allowlist)
        assert result is True

    def test_should_include_path_dir_pattern(self) -> None:
        """Test directory pattern matching."""
        path = REPO_ROOT / "backend" / "src" / "oc8" / "runtime" / "test.py"
        allowlist = ["backend/src/oc8/**", "frontend/src/**"]
        result = export_edition.should_include_path(path, allowlist)
        assert result is True

    def test_should_exclude_path_git(self) -> None:
        """Test that .git is excluded."""
        path = REPO_ROOT / ".git" / "config"
        result = export_edition.should_exclude_path(path)
        assert result is True

    def test_should_exclude_path_venv(self) -> None:
        """Test that venv is excluded."""
        path = REPO_ROOT / "backend" / ".venv" / "bin" / "python"
        result = export_edition.should_exclude_path(path)
        assert result is True

    def test_should_exclude_path_cache(self) -> None:
        """Test that cache is excluded."""
        path = REPO_ROOT / "__pycache__" / "test.pyc"
        result = export_edition.should_exclude_path(path)
        assert result is True

    def test_should_exclude_path_output(self) -> None:
        """Test that output directories are excluded."""
        path = REPO_ROOT / "frontend" / ".output" / "index.html"
        result = export_edition.should_exclude_path(path)
        assert result is True

    def test_should_exclude_env_files(self) -> None:
        """`.env`/`backup.env` are kept out of every export by never being
        allowlisted (see `test_env_files_are_never_collected_only_env_example_is`
        below) rather than by `should_exclude_path` pattern-matching on ".env" --
        FORBIDDEN_PATTERNS deliberately dropped that pattern (see its own
        comment) because the crude substring matcher it uses would otherwise
        also strip the intentionally-shipped `.env.example` template."""
        path = REPO_ROOT / ".env"
        result = export_edition.should_exclude_path(path)
        assert result is False


class TestExportFileCollection:
    """Test file collection for export."""

    def test_collect_community_files(self) -> None:
        """Test collecting files for Community export."""
        config = export_edition.ExportConfig(edition="community")
        result = export_edition.ExportResult(
            edition="community",
            success=False,
            timestamp="2026-08-04T10:00:00+00:00",
            dry_run=True,
        )
        files = export_edition.collect_export_files(config, result)

        # Verify file count is reasonable
        assert result.files_counted > 100
        assert len(files) == result.files_counted

        # Verify backend and frontend are included
        file_paths = [str(f.relative_to(REPO_ROOT)) for f in files]
        assert any("backend/src/oc8" in p for p in file_paths)
        assert any("frontend/src" in p for p in file_paths)

        # Verify enterprise backend/frontend sources are not included
        # (Note: we may have enterprise/backend/pyproject.toml as reference, but no source)
        assert not any(p.startswith("enterprise/backend/src") or p.startswith("enterprise/frontend/src") for p in file_paths)

    def test_collect_enterprise_files(self) -> None:
        """Test collecting files for Enterprise export."""
        config = export_edition.ExportConfig(edition="enterprise")
        result = export_edition.ExportResult(
            edition="enterprise",
            success=False,
            timestamp="2026-08-04T10:00:00+00:00",
            dry_run=True,
        )
        files = export_edition.collect_export_files(config, result)

        # Enterprise should have much fewer files than Community
        assert result.files_counted < 100

        # Verify enterprise is included
        file_paths = [str(f.relative_to(REPO_ROOT)) for f in files]
        assert any("enterprise" in p for p in file_paths)

    def test_no_forbidden_patterns_in_collection(self) -> None:
        """Test that forbidden patterns are excluded."""
        config = export_edition.ExportConfig(edition="community")
        result = export_edition.ExportResult(
            edition="community",
            success=False,
            timestamp="2026-08-04T10:00:00+00:00",
            dry_run=True,
        )
        files = export_edition.collect_export_files(config, result)

        # Check that no .git, .venv, or __pycache__ are included
        file_paths = [str(f) for f in files]
        assert not any(".git" in p for p in file_paths)
        assert not any(".venv" in p for p in file_paths)
        assert not any("__pycache__" in p for p in file_paths)
        # A precise ".env" suffix check, not a substring check: the allowlist
        # deliberately ships ".env.example" (see COMMUNITY_ALLOWLIST), which
        # contains ".env" as a substring but must not be caught here.
        assert not any(p.endswith(".env") for p in file_paths)

    def test_env_files_are_never_collected_only_env_example_is(self) -> None:
        """The real guarantee behind dropping ".env" from FORBIDDEN_PATTERNS:
        a real `.env`/`backup.env` can never reach an export because nothing
        in COMMUNITY_ALLOWLIST names it -- only the checked-in `.env.example`
        template is allowlisted."""
        config = export_edition.ExportConfig(edition="community")
        result = export_edition.ExportResult(
            edition="community",
            success=False,
            timestamp="2026-08-04T10:00:00+00:00",
            dry_run=True,
        )
        files = export_edition.collect_export_files(config, result)
        file_paths = [str(f.relative_to(REPO_ROOT)) for f in files]

        assert ".env.example" in file_paths
        assert ".env" not in file_paths
        assert not any(p.endswith(".env") for p in file_paths)


class TestAllowlistPathsResolve:
    """Regression test: allowlist entries must resolve to real paths on disk.

    `collect_export_files` silently no-ops for any allowlist entry whose path
    doesn't exist (no warning, no error) — e.g. when a directory referenced by
    the allowlist gets renamed (as happened when plugins/ became capas/) and the
    allowlist isn't updated to match, the export just quietly ships fewer files.
    This guards against that class of silent breakage recurring.

    Scoped to the `capas/` entries specifically (the ones this rename touched),
    not the full allowlist: a handful of unrelated, pre-existing entries (e.g.
    `backend/requirements.txt`, which this repo has never actually shipped —
    dependencies live in `backend/pyproject.toml` instead) are already dangling
    for reasons unrelated to the plugins->capas rename and are out of scope here.
    """

    def test_community_allowlist_capa_entries_exist(self) -> None:
        """The Community allowlist's `capas` entry must resolve on disk.

        As of the 2026-08-27 repo-split decision every plugin ships open (the
        marketplace/commercial-plugin trust model doesn't exist yet, so
        nothing commercial can ship here today regardless) -- so the
        allowlist names the whole `capas` directory, not a per-plugin list.
        """
        assert "capas" in export_edition.COMMUNITY_ALLOWLIST["dirs"], (
            "expected a 'capas' entry in the Community allowlist"
        )
        path = REPO_ROOT / "capas"
        assert path.exists(), (
            f"Community allowlist entry 'capas' does not resolve to a real "
            f"directory at {path} — collect_export_files() will silently drop "
            "it from the export."
        )


class TestExportOperation:
    """Test the export operation itself."""

    def test_dry_run_creates_no_files(self, tmp_path: Path) -> None:
        """Test that dry-run doesn't create files."""
        config = export_edition.ExportConfig(
            edition="community",
            output_dir=tmp_path / "output",
            dry_run=True,
        )
        result = export_edition.run_export(config)

        # Dry-run should not create output directory
        assert result.files_copied == 0
        # If output_dir exists, it's empty or not created
        if config.output_dir:
            if config.output_dir.exists():
                assert len(list(config.output_dir.iterdir())) == 0

    def test_export_to_empty_directory(self, tmp_path: Path) -> None:
        """Test exporting to an empty directory."""
        output_dir = tmp_path / "fresh-export"
        config = export_edition.ExportConfig(
            edition="community",
            output_dir=output_dir,
            dry_run=False,
        )
        result = export_edition.run_export(config)

        # Should succeed
        assert result.success
        assert output_dir.exists()
        assert result.files_copied > 0

        # Verify some key files exist
        assert (output_dir / "backend" / "src" / "oc8").is_dir()
        assert (output_dir / "frontend" / "src").is_dir()

    def test_export_rejects_existing_directory_without_overwrite(
        self, tmp_path: Path
    ) -> None:
        """Test that export rejects existing directory without --overwrite."""
        output_dir = tmp_path / "existing"
        output_dir.mkdir()
        (output_dir / "somefile").touch()

        config = export_edition.ExportConfig(
            edition="community",
            output_dir=output_dir,
            dry_run=False,
            overwrite=False,
        )
        result = export_edition.run_export(config)

        # Should fail
        assert result.success is False
        assert len(result.violations) > 0
        assert "Output directory exists" in result.violations[0]

    def test_export_allows_overwrite_with_flag(self, tmp_path: Path) -> None:
        """Test that export allows overwriting with --overwrite flag."""
        output_dir = tmp_path / "to-overwrite"
        output_dir.mkdir()
        (output_dir / "old-file").touch()

        config = export_edition.ExportConfig(
            edition="community",
            output_dir=output_dir,
            dry_run=False,
            overwrite=True,
        )
        result = export_edition.run_export(config)

        # Should succeed
        assert result.success
        assert output_dir.exists()
        # Old file might still be there (we don't delete), but new files should exist
        assert result.files_copied > 0


class TestValidation:
    """Test validation checks during export."""

    def test_forbidden_directories_warning(self) -> None:
        """Test that forbidden directories in monorepo produce warnings."""
        config = export_edition.ExportConfig(edition="community")
        result = export_edition.ExportResult(
            edition="community",
            success=True,
            timestamp="2026-08-04T10:00:00+00:00",
            dry_run=True,
        )

        # Run validation
        export_edition.validate_export(config, result)

        # Should have warning about enterprise directory
        if (REPO_ROOT / "enterprise").exists():
            assert len(result.warnings) > 0
            assert any("enterprise" in w for w in result.warnings)


class TestCommunityExportBoundary:
    """Test Community edition export boundaries."""

    def test_community_excludes_enterprise_source(self, tmp_path: Path) -> None:
        """Test that Community export excludes Enterprise source."""
        config = export_edition.ExportConfig(
            edition="community",
            output_dir=tmp_path / "community",
            dry_run=False,
        )
        result = export_edition.run_export(config)

        assert result.success
        output_dir = tmp_path / "community"

        # Verify enterprise/ is not in output
        assert not (output_dir / "enterprise").exists()

        # Verify saas/ is not in output
        assert not (output_dir / "saas").exists()

    def test_community_excludes_saas_source(self, tmp_path: Path) -> None:
        """Test that Community export excludes SaaS source."""
        config = export_edition.ExportConfig(
            edition="community",
            output_dir=tmp_path / "community",
            dry_run=False,
        )
        result = export_edition.run_export(config)

        assert result.success
        output_dir = tmp_path / "community"
        assert not (output_dir / "saas").exists()


class TestEnterpriseExportBoundary:
    """Test Enterprise edition export boundaries."""

    def test_enterprise_excludes_saas_source(self, tmp_path: Path) -> None:
        """Test that Enterprise export excludes SaaS source."""
        config = export_edition.ExportConfig(
            edition="enterprise",
            output_dir=tmp_path / "enterprise",
            dry_run=False,
        )
        result = export_edition.run_export(config)

        assert result.success
        output_dir = tmp_path / "enterprise"
        assert not (output_dir / "saas").exists()

    def test_enterprise_includes_enterprise_source(self, tmp_path: Path) -> None:
        """Test that Enterprise export includes Enterprise source."""
        config = export_edition.ExportConfig(
            edition="enterprise",
            output_dir=tmp_path / "enterprise",
            dry_run=False,
        )
        result = export_edition.run_export(config)

        assert result.success
        output_dir = tmp_path / "enterprise"
        # Enterprise backend should be included
        enterprise_path = output_dir / "enterprise" / "backend"
        assert enterprise_path.exists()


class TestExportIntegration:
    """Integration tests for export operations."""

    def test_all_editions_export_successfully(self, tmp_path: Path) -> None:
        """Test that all three editions can be exported successfully."""
        for edition in ["community", "enterprise", "saas"]:
            output_dir = tmp_path / f"{edition}-export"
            config = export_edition.ExportConfig(
                edition=edition,
                output_dir=output_dir,
                dry_run=False,
            )
            result = export_edition.run_export(config)

            assert result.success, f"Export of {edition} failed: {result.violations}"
            assert output_dir.exists()
            assert result.files_copied > 0

    def test_exports_are_independent(self, tmp_path: Path) -> None:
        """Test that exports are independent (don't cross-pollinate)."""
        community_dir = tmp_path / "community"
        enterprise_dir = tmp_path / "enterprise"

        # Export community
        config_c = export_edition.ExportConfig(
            edition="community",
            output_dir=community_dir,
            dry_run=False,
        )
        result_c = export_edition.run_export(config_c)
        assert result_c.success

        # Export enterprise
        config_e = export_edition.ExportConfig(
            edition="enterprise",
            output_dir=enterprise_dir,
            dry_run=False,
        )
        result_e = export_edition.run_export(config_e)
        assert result_e.success

        # Community should not have enterprise
        assert not (community_dir / "enterprise").exists()

        # Enterprise should have enterprise
        assert (enterprise_dir / "enterprise").exists()

    def test_json_export_output(self, tmp_path: Path) -> None:
        """Test JSON export output."""
        config = export_edition.ExportConfig(
            edition="community",
            dry_run=True,
        )
        result = export_edition.run_export(config)
        result_dict = result.to_dict()

        # Should be JSON-serializable
        json_str = json.dumps(result_dict)
        parsed = json.loads(json_str)

        assert parsed["edition"] == "community"
        assert parsed["success"] is True
        assert "timestamp" in parsed
        assert parsed["dry_run"] is True
