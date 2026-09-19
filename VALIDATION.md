# Validation for v0.5

Checked on 2026-09-19 in a Linux Python 3.12 environment. macOS installation was not exercised here.

- Original v0.4 self_test.py: passed (seven routing checks plus compensation, hours and Oracle date checks).
- Nine new integration tests: passed (routing overrides, missing optional dependency, unlimited-result propagation, nested JobPosting data, expiry, compensation and missing-value handling).
- Streamlit batch test: passed. A simulated failed first board did not prevent the second board from exporting a result. This is a UI orchestration test, not a live browser test.
- Live Greenhouse board at https://boards.greenhouse.io/anthropic: returned two requested rows, both with descriptions.
- Live Ashby board at https://jobs.ashbyhq.com/openai: returned two requested rows, both with descriptions.
- Actual upstream package at the pinned Git revision was installed for testing with base, aiohttp, html2text, pycryptodome and SOCKS HTTP support. The full optional scrapers extra includes additional engines not exercised here.
- Upstream registry contained 70 adapters. Only the two sources above were live-tested in this session. No whole-board completeness or aggregate success percentage is claimed.
- Chromium download timed out in this environment, so configured browser pagination, frame discovery, screenshots and browser extraction require local validation. Existing HOPE adapters were not live-retested.

The first live attempt exposed a missing SOCKS dependency in this environment. Adding httpx[socks] resolved it; both live checks subsequently succeeded. Details of the successful checks are in live_validation.json.

Run from the extracted folder:

```bash
python self_test.py
python -m unittest test_integration.py test_batch_ui.py
```

The batch UI test writes fixture output to outputs/. Run it before collecting production results or in a clean copy. The optional ATS dependency is required for actual upstream scraping.

## v0.6 local OCR checks

The original self-tests and v0.5 integration/UI tests pass. Nine additional OCR tests pass, including actual Tesseract recognition of a generated posting image and correct extraction of its title, USD hourly wage range, weekly hours and required EPA certification. The rules also leave preferred credentials and unsupported currencies blank, reject blocked/listing pages, honor low-confidence filtering, preserve existing fields, and route adapter failures into the fallback. No hosted AI call was made. Full live-browser capture remains unverified here because Chromium was unavailable; run the new fallback locally on representative postings before relying on its exports.
