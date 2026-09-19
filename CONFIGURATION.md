# Board configuration

The app reads board_configs.json beside the Python files. Keys must match the pasted board URL exactly. Existing HOPE-specific adapters are preserved. Installed upstream adapters handle recognized additional URLs. Unknown boards use the browser fallback.

## Custom ATS domains

Use an explicit upstream adapter and tenant when the URL cannot identify its platform:

```json
{
  "boards": {
    "https://careers.example.org/": {
      "ats": "greenhouse",
      "slug": "actual-board-token",
      "board_timeout_seconds": 180,
      "adapter_options": {"timeout": 30, "include_descriptions": true}
    }
  }
}
```

This is a template, not a working employer configuration. Follow the pinned repository's adapter documentation for each slug and adapter-specific option. Workday typically expects the full tenant careers URL, not just the company name. An explicit override also lets you choose an upstream adapter instead of a v0.4 adapter for a particular URL.

## Custom browser boards

```json
{
  "boards": {
    "https://example.org/careers": {
      "browser": {
        "job_link_selector": "a.job-link",
        "next_page_selector": "button.next-page",
        "title_selector": "h1",
        "description_selector": "article.job-description",
        "employer_name": "Confirmed employer name",
        "max_pages": 10,
        "wait_ms": 1500,
        "timeout_ms": 60000,
        "include_iframes": false,
        "allow_external_links": false,
        "headless": true
      }
    }
  }
}
```

Selectors must be inspected on the actual website. A next-page or load-more control can be configured; endless scrolling without a button needs another adapter. Optional wait_selector applies to both listing and detail pages, so use only a selector present on both. iframe discovery and external links require explicit opt-in. Avoid very broad link selectors.

The generic fallback scans up to max_pages. Without a next-page selector it inspects only the first listing page. Removing the job limit does not guarantee full coverage. A JobPosting JSON-LD node or configured description selector is required before a generic result is exported. Missing compensation, stage, credentials and hours are left blank. Screenshot and HTML evidence support manual review; local screenshot OCR is available when enabled; see OCR_SETUP.md.

## Result limits and diagnostics

The ATS library fetches a board before this wrapper applies the requested row limit. A five-job test can still request the entire listing and descriptions. A subprocess enforces a 180-second total limit by default. Increase board_timeout_seconds for a known large board. An upstream failure is reported. When local OCR is enabled, browser extraction and OCR are attempted and labeled for review.

outputs/board_results.csv records each board's status. A successful response means jobs were returned, not that every job was found. Browser diagnostics list rejected pages and errors. Compensation normalization retains original currency and uses the existing 2080-hour/year, 40-hour/week and 8-hour/day assumptions; it is not currency conversion. Required education, certifications and category remain blank for the new upstream mapping pending evidence-based HOPE field rules. Existing adapters retain their original extraction rules.

## Scenarios to validate for each employer

- Listing URLs versus individual posting URLs; tenants and regions.
- First, middle and final listing pages; repeat/empty pages and load-more controls.
- Job links inside frames or pointing to another employer ATS.
- Missing descriptions, closed jobs, duplicated postings and expired dates.
- Hourly versus annual pay; missing currency; salary ranges and missing bounds.
- HTTP 403/429, login pages, challenge pages, slow responses and changed layouts.
- Required versus preferred qualifications and multiple locations.

Compare a sampled set to the original postings and record discovered/exported counts. Do not treat CAPTCHA/login pages as jobs or mark a board verified solely because its platform is supported.
