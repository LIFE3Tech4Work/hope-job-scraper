import argparse
from pathlib import Path

from scraper_router import scrape_jobs
from ocr_fallback import OCRSettings
import os


def main():
    parser = argparse.ArgumentParser(description="ATS-aware local job scrubber")
    parser.add_argument("url")
    parser.add_argument("--max-jobs", type=int, default=None)
    parser.add_argument("--no-debug", action="store_true")
    parser.add_argument("--ocr", action="store_true", help="Use local Tesseract screenshots to recover missing fields")
    parser.add_argument("--screenshot-only", action="store_true", help="Treat URL as a single posting; requires --ocr")
    parser.add_argument("--ocr-max-postings",type=int,default=5)
    args = parser.parse_args()

    def progress(i, total, title):
        print(f"[{i}/{total}] {title}")

    df, platform = scrape_jobs(
        args.url,
        max_jobs=args.max_jobs,
        save_debug=not args.no_debug,
        progress_callback=progress,
        ocr=OCRSettings(enabled=args.ocr,max_postings=args.ocr_max_postings),
        screenshot_only=args.screenshot_only,
    )

    Path("outputs").mkdir(exist_ok=True)
    df.to_csv("outputs/job_opportunities.csv", index=False)
    df.to_excel("outputs/job_opportunities.xlsx", index=False, engine="openpyxl")

    populated = df["Original Min Compensation"].notna().sum() if len(df) else 0
    print(f"Platform: {platform}")
    print(f"Saved {len(df)} jobs")
    print(f"Compensation populated: {populated}/{len(df)}")
    print("outputs/job_opportunities.csv")
    print("outputs/job_opportunities.xlsx")


if __name__ == "__main__":
    main()
