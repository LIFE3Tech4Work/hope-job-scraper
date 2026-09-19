# Local Job Scrubber v0.6

Extends v0.4 with optional GitHub ATS adapters, per-board configuration, stricter generic extraction and batch failure reporting. CSV/Excel columns remain compatible with the supplied v0.4 schema.

## Install on macOS

Use Python 3.11 or newer and Git for the optional integration. Extract this ZIP to a new folder; your v0.4 folder can remain available.

```bash
cd job-scrubber-local-v0.6
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-ats.txt
python -m playwright install chromium
python self_test.py
python -m unittest test_integration.py
bash run.sh
```

Open http://127.0.0.1:8501. For the original dependency set only, install requirements.txt instead; the extra ATS integration will be unavailable.

## GitHub integration

Repository: https://github.com/kalil0321/ats-scrapers
Pinned revision: 6b44a1badc9bfbf5cf176f75265cc5729e520e99
Upstream package version at this revision: 0.3.0; MIT license.

The inspected revision registers 70 source adapters. See UPSTREAM_ADAPTERS.md. Not all are auto-detectable from arbitrary URLs; some need explicit tenant settings, additional dependencies, credentials or source-specific configuration. Adapter availability is not a live success rate. The dependency is installed from GitHub, not bundled or automatically updated.

Existing ADP Workforce Now, ADP MyJobs, Oracle Recruiting Cloud, CAMBA, iCIMS, Paycom and Brooklyn Navy Yard adapters stay first in the default routing order. Additional recognized ATS URLs use the library. Exact-URL overrides can choose a different adapter. Unknown URLs use the configurable browser fallback.

## What changed

- Added an optional upstream adapter bridge and exact-revision installation file.
- Mapped upstream job records into the existing HOPE export columns, preserving currency and missing values.
- Added board_configs.json for adapter/tenant overrides and browser selectors.
- Removed the generic fallback's hidden five-result cap when no limit is requested.
- Added configurable browser pagination, frame/link discovery and screenshot/HTML evidence.
- Rejected generic pages without JobPosting data or a configured job-description selector.
- Isolated batch failures and saved outputs/board_results.csv alongside the CSV and Excel jobs.
- Added total time limits for upstream runs and documented coverage limits.

## Usage

Paste one or more public career-board URLs, one per line. Start with five returned jobs per board and compare them with the source. The upstream integration may fetch the whole board before limiting returned rows. Check Board results after every run. Read CONFIGURATION.md for custom sites and qualification-field limitations.

Outputs: outputs/job_opportunities.csv, outputs/job_opportunities.xlsx and outputs/board_results.csv. The CLI accepts a single URL with --max-jobs and --no-debug. Debug evidence is local in debug/.

## Scope

This package does not guarantee every board or every posting. It does not create or publish a GitHub repository. It adds a pinned dependency on an existing public repository. JobSpy is a possible separate search-mode extension for aggregated boards, not a drop-in replacement for employer-URL scraping. Crawl4AI is a possible future extraction framework; it is not integrated in this version. Local screenshot OCR is available; see OCR_SETUP.md.

See VALIDATION.md for the actual checks performed. Live results depend on the website and environment.

## New in v0.6

Optional fully local Tesseract screenshot OCR recovers missing job fields, retains screenshots and recognized text, and flags results for review. No hosted AI API is used. On macOS, run `brew install tesseract`, then enable Local screenshot OCR fallback in the app. See OCR_SETUP.md for behavior, limitations, evidence files and CLI options.
