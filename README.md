# AI Spend & Reliability Analytics

Capture model-call traces and analyze estimated spend, successful-answer economics, latency and failure patterns.

## Implementation and validation

Local tracing, warehouse, findings and Streamlit app are implemented. Hosted telemetry collection and live-model billing reconciliation are not verified.

Automated checks: **9 tests**. The GitHub Actions run linked above the file browser is the current CI result. Local checks and external integrations are separate claims.

## Reproduce locally

Use Python 3.12. Run from this repository’s root in a fresh virtual environment.

```sh
python -m venv .venv
# Activate .venv for your shell, then:
python -m pip install -r requirements.txt
```

For repositories using `src/`, set the import path before running commands:

```powershell
# PowerShell
$env:PYTHONPATH="src"
```
```sh
# macOS/Linux
export PYTHONPATH=src
```

```sh
python src/tokenledger/generate_traces.py
python -m pytest tests -q
```

## Open the local application

```sh
python -m streamlit run dashboard/app.py
```

## Data and interpretation

The scaled corpus is synthetic. Mock traces priced with a model tariff are estimated spend, not an invoice. Seeded prompt-version differences are not a controlled live experiment.

## Inspect the work

- [`tests/`](tests/) — executable checks and examples.
- [`docs/`](docs/) — methodology, integration specifications and the historical design.
- [Portfolio](https://raveesh-rajg.github.io/) — project directory.

## Completion boundary

Passing local tests establishes the checks listed in this repository. It does not establish cloud deployment, real-data quality, production security, or native BI rendering unless an explicit verification record says so.
