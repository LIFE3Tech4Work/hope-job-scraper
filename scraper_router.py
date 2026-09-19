from typing import Callable, Optional, Tuple
from urllib.parse import urlparse

import pandas as pd

from adp_scraper import scrape_adp
from adp_myjobs_scraper import scrape_adp_myjobs
from camba_scraper import scrape_camba
from icims_scraper import scrape_icims
from paycom_scraper import scrape_paycom
from brooklyn_navy_yard_scraper import scrape_brooklyn_navy_yard
from generic_scraper import scrape_generic
from oracle_scraper import scrape_oracle
from schema import CANONICAL_COLUMNS
from ats_bridge import board_config, resolve, scrape_upstream


def detect_platform(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Enter a complete http:// or https:// URL")
    cfg = board_config(url)
    if cfg.get("ats") and cfg.get("slug"):
        return "ATS library / " + cfg["ats"]
    host = parsed.hostname.lower()
    path = urlparse(url).path.lower()
    if "workforcenow.adp.com" in host or "/mascsr/" in path:
        return "ADP Workforce Now"
    if host == "myjobs.adp.com" and "cx" in path.strip("/").split("/"):
        return "ADP MyJobs"
    if "oraclecloud.com" in host and "candidateexperience" in path:
        return "Oracle Recruiting Cloud"
    if "camba.org" in host and "/careers" in path:
        return "CAMBA Careers"
    if "icims.com" in host:
        return "iCIMS"
    if "paycomonline.net" in host:
        return "Paycom"
    if "brooklynnavyyard.org" in host and "jobs-with-yard-businesses" in path:
        return "Brooklyn Navy Yard aggregator"
    resolved = resolve(url)
    if resolved:
        return "ATS library / " + resolved.ats.value
    if "greenhouse.io" in host or "boards.greenhouse.io" in host:
        return "Greenhouse (generic fallback)"
    if "lever.co" in host or "jobs.lever.co" in host:
        return "Lever (generic fallback)"
    if "myworkdayjobs.com" in host or "workdayjobs.com" in host:
        return "Workday (generic fallback)"
    return "Generic / Unknown"


def _scrape_jobs(
    url: str,
    max_jobs: Optional[int] = None,
    debug_dir: str = "debug",
    save_debug: bool = True,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ocr=None,
) -> Tuple[pd.DataFrame, str]:
    platform = detect_platform(url)
    if platform.startswith("ATS library / "):
        df = scrape_upstream(url, max_jobs=max_jobs, debug_dir=debug_dir, save_debug=save_debug, progress_callback=progress_callback)
    elif platform == "ADP Workforce Now":
        df = scrape_adp(url, max_jobs=max_jobs, debug_dir=debug_dir, save_debug=save_debug, progress_callback=progress_callback)
    elif platform == "ADP MyJobs":
        df = scrape_adp_myjobs(url, max_jobs=max_jobs, debug_dir=debug_dir, save_debug=save_debug, progress_callback=progress_callback)
    elif platform == "Oracle Recruiting Cloud":
        df = scrape_oracle(url, max_jobs=max_jobs, debug_dir=debug_dir, save_debug=save_debug, progress_callback=progress_callback)
    elif platform == "CAMBA Careers":
        df = scrape_camba(url, max_jobs=max_jobs, debug_dir=debug_dir, save_debug=save_debug, progress_callback=progress_callback)
    elif platform == "iCIMS":
        df = scrape_icims(url, max_jobs=max_jobs, debug_dir=debug_dir, save_debug=save_debug, progress_callback=progress_callback)
    elif platform == "Paycom":
        df = scrape_paycom(url, max_jobs=max_jobs, debug_dir=debug_dir, save_debug=save_debug, progress_callback=progress_callback)
    elif platform == "Brooklyn Navy Yard aggregator":
        df = scrape_brooklyn_navy_yard(url, max_jobs=max_jobs, debug_dir=debug_dir, save_debug=save_debug, progress_callback=progress_callback)
    else:
        df = scrape_generic(url, max_jobs=max_jobs, debug_dir=debug_dir, save_debug=save_debug, progress_callback=progress_callback, ocr=ocr)
    return df.reindex(columns=CANONICAL_COLUMNS), platform


def scrape_jobs(url, max_jobs=None, debug_dir="debug", save_debug=True, progress_callback=None,
                ocr=None, screenshot_only=False):
    from ocr_fallback import scrape_posting_screenshot
    # Validate URL before any browser/API use. Invalid input is not retried.
    platform = detect_platform(url)
    if screenshot_only:
        if not ocr or not ocr.enabled: raise ValueError("Enable screenshot extraction first")
        ocr.validate()
        return scrape_posting_screenshot(url, ocr, debug_dir), "Screenshot posting"
    if ocr and ocr.enabled: ocr.validate()
    try:
        df, used = _scrape_jobs(url,max_jobs,debug_dir,save_debug,progress_callback,ocr)
        if df.empty and ocr and ocr.enabled:
            raise RuntimeError("Primary adapter returned no jobs")
    except Exception as original:
        if not ocr or not ocr.enabled or "generic" in platform.lower() or platform=="Generic / Unknown":
            raise
        try:
            df = scrape_generic(url,max_jobs=max_jobs,debug_dir=debug_dir,save_debug=save_debug,
                                progress_callback=progress_callback,ocr=ocr)
            df.attrs.setdefault("warnings",[]).append("Primary adapter failed; browser/screenshot fallback used: "+str(original))
            used=platform+" -> browser/screenshot fallback"
        except Exception as fallback:
            raise RuntimeError(f"Primary extraction failed: {original}. Browser/screenshot fallback failed: {fallback}") from fallback
    # Recover missing fields on known posting URLs without overwriting
    # already populated fields. The posting budget bounds browser/OCR work.
    if ocr and ocr.enabled:
        df=df.astype(object)
        for index,row in df.iterrows():
            if "Local screenshot OCR" in str(row.get("Source")): continue
            if ocr.postings_used>=ocr.max_postings:
                df.attrs.setdefault("warnings",[]).append("OCR posting limit reached; remaining missing fields were not checked.")
                break
            targets=["Job Description","Employer Name","Original Min Compensation","Min Hours per Week","Required Education","Required Certifications & Licenses"]
            if all(pd.notna(row.get(k)) and str(row.get(k)).strip() for k in targets): continue
            detail=row.get("Job Detail URL")
            if pd.isna(detail) or not str(detail).startswith(("https://","http://")): continue
            try:
                extra=scrape_posting_screenshot(str(detail),ocr,debug_dir,title_hint=str(row.get("Job Opportunity Name") or ""),verified_posting=True)
                comp_keys={"Original Min Compensation","Original Max Compensation","Min Wage Normalized Hourly","Max Wage Normalized Hourly","Compensation Currency","Compensation Period","Compensation Source","Compensation Evidence"}
                has_comp=any(pd.notna(row.get(k)) and str(row.get(k)).strip() for k in ["Original Min Compensation","Original Max Compensation"])
                for key,value in extra.iloc[0].items():
                    if key in comp_keys and has_comp: continue
                    if pd.isna(df.at[index,key]) or not str(df.at[index,key]).strip(): df.at[index,key]=value
                df.at[index,"Source"] = (str(row.get("Source")) if pd.notna(row.get("Source")) else used)+" + Local screenshot OCR - review required"
                df.attrs.setdefault("ocr_audits",[]).extend(extra.attrs.get("ocr_audits",[]))
                df.attrs.setdefault("warnings",[]).extend(extra.attrs.get("warnings",[]))
            except Exception as exc:df.attrs.setdefault("warnings",[]).append(f"Screenshot recovery failed for {detail}: {exc}")
    return df,used
