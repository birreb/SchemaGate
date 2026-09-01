"""The command line, which is how anyone runs this without reading the Dockerfile."""

import pytest

from schemagate.cli import main

DSN = "postgresql://user:password@localhost:5432/billing"


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHEMAGATE_CONNECTIONS__primary", DSN)


def test_a_subcommand_is_required() -> None:
    """Bare `schemagate` should say what it can do rather than start a server."""
    with pytest.raises(SystemExit):
        main([])


def test_check_reports_what_is_configured(
    configured: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check"]) == 0

    printed = capsys.readouterr().out
    assert "connections: primary" in printed
    assert "effort: low" in printed


def test_check_never_prints_a_connection_string(
    configured: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """It answers what is configured, and a DSN is a credential."""
    main(["check"])

    assert "password" not in capsys.readouterr().out


def test_check_says_what_is_missing_rather_than_implying_it_is_set(
    configured: None, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["check"])

    printed = capsys.readouterr().out
    assert "none, so documents needing a model are refused" in printed
    assert "none, so cost is reported null" in printed
    assert "none, so the endpoints are open" in printed


def test_a_missing_connection_is_reported_and_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SCHEMAGATE_CONNECTIONS__primary", raising=False)
    monkeypatch.delenv("SCHEMAGATE_CONNECTIONS", raising=False)

    assert main(["check"]) == 1
    assert "schemagate:" in capsys.readouterr().err


def test_evaluate_runs_the_free_cases_without_a_provider(
    configured: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run with no provider still scores the paths that never call one."""
    code = main(["evaluate", "--cases", "evals/cases"])

    printed = capsys.readouterr().out
    assert "invoices-csv" in printed
    assert "48/48" in printed, "both tabular cases read every cell correctly"
    assert code == 1, "the PDF case needs a model, so the run is not clean"


def test_evaluate_exits_zero_only_when_every_case_is_clean(
    configured: None, tmp_path: object, capsys: pytest.CaptureFixture[str]
) -> None:
    """Non-zero is what makes this usable as a gate on a model change."""
    import json
    import pathlib
    import shutil

    directory = pathlib.Path(str(tmp_path)) / "cases"
    directory.mkdir()
    source = pathlib.Path("evals/cases/01_invoices_csv.json")
    shutil.copy(source, directory / source.name)

    assert main(["evaluate", "--cases", str(directory)]) == 0

    broken = json.loads(source.read_text(encoding="utf-8"))
    broken["expected"][0]["total"] = "0.00"
    (directory / source.name).write_text(json.dumps(broken), encoding="utf-8")

    assert main(["evaluate", "--cases", str(directory)]) == 1
    assert "wanted" in capsys.readouterr().out
