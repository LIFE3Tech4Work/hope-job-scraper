# Local screenshot OCR fallback

Version 0.6 uses the Tesseract open-source OCR engine directly through its command-line interface. No OpenAI API, hosted model, API key or per-call fee is used. Website access still needs internet; recognizing screenshot text and extracting fields happen on your computer.

Repository: https://github.com/tesseract-ocr/tesseract
License: Apache 2.0. Mature engine with a large contributor/user community, multilingual recognition, and structured TSV output. Direct CLI integration avoids adding a Python OCR service or model server. PaddleOCR was considered, but Tesseract is a smaller addition to this existing browser application and easy to install on macOS. This selection is not a claim that it wins every OCR benchmark.

## macOS setup

Install the engine once (Homebrew must already be installed):

```bash
brew install tesseract
tesseract --version
```

Then install and launch the new ZIP as described in README.md. No new Python dependency is needed for production OCR; Pillow is used only by the image test below.

In the app, expand Local screenshot OCR fallback and check Enable local screenshot OCR with Tesseract. Start with five postings and six screenshots per posting. The language defaults to eng. Other languages require the corresponding Tesseract language files. The checkbox defaults off so a missing native installation does not break ordinary scraping.

CLI examples:

```bash
python cli.py 'https://example.org/careers' --ocr --max-jobs 5
python cli.py 'https://example.org/jobs/123' --ocr --screenshot-only
```

These are URL placeholders; replace them with actual employer URLs.

## When it runs

1. The existing ATS or browser scraper runs first.
2. If a dedicated scraper fails or returns zero jobs, the browser fallback discovers candidate job links and attempts structured extraction, then screenshot OCR when no usable title/description is available.
3. For returned job records missing description, employer, pay, weekly hours, required education or required certifications, the router opens their detail URLs and attempts OCR within the posting budget.
4. Only blank fields are filled. Existing compensation is kept together as a group, avoiding mixing one scraper's minimum with an OCR maximum from a different pay range.
5. Direct posting mode lets you force screenshot extraction for an individual URL, including testing fields that other extraction paths omit.

A posting limit bounds browser/OCR work across a batch. Pages beyond that limit retain missing fields. No missing field is guaranteed recoverable: the posting may not state it at all.

## Capture and extraction

The browser uses a 1440 by 1000 viewport and scrolls in overlapping 800-pixel steps. Each screenshot is read locally by Tesseract. TSV output includes word positions and confidence scores; lines containing a word below the threshold (65 by default) are excluded from automatic field extraction. Confidence measures recognition, not factual correctness.

Rules recognize explicit title/company/location/date/ID labels, short occupational title headings, visible description sections, explicit hourly/annual/weekly/monthly/daily compensation, weekly hours, required education/certification statements, explicit job term/status and vaccination requirements. Plain $ does not establish USD. Full-time does not establish permanent or 40 hours. Preferred qualifications are not recorded as required. Unsupported layouts and ambiguous statements stay blank.

Employer names without an identifying label, dates embedded in prose, complex compensation, and requirements split across lines may need manual review. OCR text can include navigation or related content. The extractor is rule-based, not a general semantic AI. It does not translate, infer national occupational categories, solve CAPTCHAs, bypass logins, or discover links from pixels. If job-link discovery fails, supply the direct posting URL or configure selectors.

## Evidence and review

The app writes debug/ocr/<posting-and-time>/ containing:
- viewport PNG screenshots;
- Tesseract TSV output with coordinates and confidence;
- ocr_text.txt;
- extraction.json with field quotes, source image paths, confidence and review status.

Evidence is saved even when the general raw-debug checkbox is off. outputs/ocr_evidence.json indexes successful OCR extractions and is downloadable from the app. Job rows are labeled Local screenshot OCR - review required. Capture-limit flags identify possible missing content below the captured region. Nested scrolling panels, cookie banners, closed accordions and delayed rendering may require manual intervention or custom page handling.

The existing CSV/Excel column schema remains unchanged. No screenshots are transmitted to an external OCR/AI provider.

## Tests

```bash
python -m pip install Pillow
python -m unittest test_ocr.py
```

Tests include actual local Tesseract recognition of a generated posting image, missing-field merge behavior, confidence filtering, blocked/listing rejection, budget enforcement, and fallback routing. These tests are not evidence of success on every job board.
