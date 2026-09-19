# Repository selection

## Integrated

https://github.com/kalil0321/ats-scrapers

Best fit for the current employer-career-URL workflow. The inspected code has a scraper registry, deterministic ATS URL resolution, typed job records and a per-source adapter architecture. It complements the existing HOPE-specific adapters. This integration calls scrapers for the user-supplied board, not the hosted dataset. See requirements-ats.txt for the exact revision.

## Possible separate extensions

https://github.com/speedyapply/JobSpy

Searches major aggregated job boards using role and location parameters. Useful for a future job-search mode, but its query interface is different from this application's employer-URL input. Not integrated in v0.5.

https://github.com/unclecode/crawl4ai

General web crawling/extraction framework. It is not a maintained catalog of job-board-specific field mappings. Consider only if the browser fallback needs more general crawling capabilities. Not integrated in v0.5.

## Coverage approach

Track platform detection, page/detail extraction, record counts, field accuracy and completeness separately. A library listing many sources does not verify every tenant, custom domain, regional deployment, login state, or page layout. Pin updates and rerun fixtures plus a representative live-board sample before adopting a new upstream revision.
