import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import pandas as pd
from playwright.sync_api import sync_playwright

from schema import CANONICAL_COLUMNS


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    value = str(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = value.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", value).strip()


def get_param(url: str, name: str) -> Optional[str]:
    vals = parse_qs(urlparse(url).query).get(name)
    return vals[0] if vals else None


def flatten(obj: Any, path: str = "root") -> List[Tuple[str, Any]]:
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}"
            out.append((p, v))
            out.extend(flatten(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            p = f"{path}[{i}]"
            out.append((p, v))
            out.extend(flatten(v, p))
    return out


def first_by_key(obj: Any, keys: List[str]) -> Any:
    wanted = {k.lower() for k in keys}
    for path, value in flatten(obj):
        key = path.rsplit(".", 1)[-1].lower()
        if key in wanted and value not in (None, "", [], {}):
            return value
    return ""


def salary_period(job: Dict[str, Any]) -> str:
    for path, value in flatten(job):
        text = f"{path} {value}".lower()
        if "salarytype" in text:
            v = clean_text(value).lower()
            if v in {"an", "annual", "annually"} or "annual" in v:
                return "Annually"
            if v in {"hr", "hour", "hourly"} or "hour" in v:
                return "Hourly"
            if "week" in v:
                return "Weekly"
            if "month" in v:
                return "Monthly"
    raw = " ".join(clean_text(v) for _, v in flatten(job) if isinstance(v, (str, int, float)))
    low = raw.lower()
    if "annually" in low or "annual" in low:
        return "Annually"
    if "hourly" in low or "per hour" in low:
        return "Hourly"
    if "weekly" in low or "per week" in low:
        return "Weekly"
    if "monthly" in low or "per month" in low:
        return "Monthly"
    return ""


def normalize_hourly(value: Any, period: str) -> Optional[float]:
    try:
        amount = float(value)
    except Exception:
        return None
    p = (period or "").lower()
    if "annual" in p or p == "an":
        return round(amount / 2080, 2)
    if "month" in p:
        return round(amount * 12 / 2080, 2)
    if "week" in p:
        return round(amount / 40, 2)
    if "day" in p:
        return round(amount / 8, 2)
    if "hour" in p:
        return round(amount, 2)
    return None


def extract_hours(text: str) -> Tuple[Optional[float], Optional[float]]:
    text = clean_text(text)
    m = re.search(r"(?:at least|minimum|min\.?)[ ]*(\d+(?:\.\d+)?)[ ]*(?:hours?|hrs?)", text, re.I)
    if m:
        return float(m.group(1)), None
    m = re.search(r"(\d+(?:\.\d+)?)[ ]*(?:-|–|to)[ ]*(\d+(?:\.\d+)?)[ ]*(?:hours?|hrs?)", text, re.I)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?)[ ]*(?:hours?|hrs?)(?:[ ]*per[ ]*week)?", text, re.I)
    if m:
        v = float(m.group(1))
        return v, v
    return None, None


def extract_education(text: str) -> str:
    low = clean_text(text).lower()
    result = []
    mapping = [
        (["high school diploma", "ged"], "High School / GED"),
        (["associate degree", "associate's degree"], "Associate Degree"),
        (["bachelor", "baccalaureate"], "Bachelor's Degree"),
        (["master's degree", "masters degree", "master degree"], "Master's Degree"),
        (["juris doctor", "doctorate", "doctoral degree", "ph.d", "phd"], "Doctorate"),
    ]
    for terms, label in mapping:
        if any(t in low for t in terms):
            result.append(label)
    return "; ".join(dict.fromkeys(result))


def extract_required_certs(text: str) -> str:
    text = clean_text(text)
    result = []
    section = text
    if "Licenses and Certifications" in text:
        section = text.split("Licenses and Certifications", 1)[1]
    patterns = [
        (r"Driver'?s License(?:[^.]{0,80})Required", "Driver's License"),
        (r"Commercial Driver'?s License(?:[^.]{0,80})Required", "Commercial Driver's License"),
        (r"\bCDL(?:\s*-?\s*Class\s*[ABC])?(?:[^.]{0,80})Required", None),
        (r"\bPMP\b(?:[^.]{0,80})Required", "PMP"),
        (r"\bCISSP\b(?:[^.]{0,80})Required", "CISSP"),
        (r"\bCISM\b(?:[^.]{0,80})Required", "CISM"),
        (r"\bGIAC\b(?:[^.]{0,80})Required", "GIAC"),
    ]
    for pat, label in patterns:
        for m in re.finditer(pat, section, re.I):
            result.append(label or clean_text(m.group(0).split("Required")[0]))
    return "; ".join(dict.fromkeys(result))


def category_from_title(title: str) -> str:
    t = f" {clean_text(title).lower()} "
    if any(x in t for x in [" engineer", "engineering"]):
        return "Engineering"
    if any(x in t for x in [" information technology", " it ", "developer", "system analyst", "cyber", "software"]):
        return "Information Technology"
    if any(x in t for x in ["maintenance", "mechanic", "technician", "building operator", "superintendent"]):
        return "Building Operations / Maintenance"
    if any(x in t for x in ["social worker", "case manager", "clinical"]):
        return "Social Services"
    if any(x in t for x in ["property manager", "property management"]):
        return "Property Management"
    return "Unclassified"


def job_term(raw_type: str, title: str) -> str:
    text = f"{raw_type} {title}".lower()
    if any(x in text for x in ["temporary", " temp ", "seasonal", "intern", "co-op", "coop"]):
        return "Temporary"
    if any(x in text for x in ["contractor", "contract role", "contract position", "fixed term"]):
        return "Contract"
    if any(x in text for x in ["full time", "full-time", "part time", "part-time", "salary", "hourly"]):
        return "Permanent"
    return ""


def vaccine_requirement(text: str) -> str:
    low = clean_text(text).lower()
    if any(x in low for x in ["vaccination required", "vaccine required", "must be vaccinated", "proof of vaccination"]):
        return "Yes"
    if any(x in low for x in ["vaccination not required", "vaccine not required"]):
        return "No"
    return "Not Stated"


def make_detail_url(search_url: str, job_id: str) -> str:
    p = urlparse(search_url)
    q = parse_qs(p.query)
    q["jobId"] = [str(job_id)]
    q["source"] = ["CC3"]
    query = urlencode(q, doseq=True)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, query, p.fragment))


def infer_employer(page_title: str, body: str) -> str:
    title = clean_text(page_title)
    for suffix in [" - Careers", " Careers", " | Recruitment", " - Recruitment", "Current Openings"]:
        title = title.replace(suffix, "").strip(" |-:")
    if title and title.lower() not in {"recruitment", "current openings"}:
        return title
    lines = [clean_text(x) for x in body.splitlines() if clean_text(x)]
    for line in lines[:20]:
        if 3 <= len(line) <= 120 and line.lower() not in {"careers", "current openings", "search jobs"}:
            return line
    return ""


def scrape_adp(
    search_url: str,
    max_jobs: Optional[int] = None,
    debug_dir: str = "debug",
    save_debug: bool = True,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> pd.DataFrame:
    cid = get_param(search_url, "cid")
    ccid = get_param(search_url, "ccId")
    lang = get_param(search_url, "lang") or "en_US"
    if not cid or not ccid:
        raise ValueError("ADP URL is missing cid or ccId.")

    parsed = urlparse(search_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    endpoint = f"{base}/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions"
    debug_root = Path(debug_dir) / "adp"
    if save_debug:
        debug_root.mkdir(parents=True, exist_ok=True)

    records = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(search_url, wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(3500)
        page_title = page.title()
        body = page.locator("body").inner_text()
        employer = infer_employer(page_title, body)

        response = context.request.get(
            endpoint,
            params={"cid": cid, "ccId": ccid, "lang": lang, "locale": lang, "$top": 1000},
            timeout=120000,
        )
        if not response.ok:
            raise RuntimeError(f"ADP jobs API failed with HTTP {response.status}")
        payload = response.json()
        jobs = payload.get("jobRequisitions") or payload.get("items") or []
        if max_jobs is not None:
            jobs = jobs[: int(max_jobs)]

        if save_debug:
            (debug_root / "search.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

        total = len(jobs)
        for idx, job in enumerate(jobs, start=1):
            title = clean_text(job.get("requisitionTitle") or job.get("Title"))
            job_id = clean_text(job.get("itemID") or job.get("Id"))
            if progress_callback:
                progress_callback(idx, total, title)

            detail_url = make_detail_url(search_url, job_id)
            detail_text = ""
            try:
                page.goto(detail_url, wait_until="domcontentloaded", timeout=120000)
                page.wait_for_timeout(2000)
                detail_text = clean_text(page.locator("body").inner_text())
            except Exception:
                pass

            pay = job.get("payGradeRange") or {}
            min_comp = pay.get("minimumRate") or pay.get("min") or pay.get("minimum")
            max_comp = pay.get("maximumRate") or pay.get("max") or pay.get("maximum")
            currency = clean_text(pay.get("currencyCode") or pay.get("currency") or "")
            period = salary_period(job)
            raw_type = clean_text(first_by_key(job, ["shortName", "workLevelCode", "jobType"]))
            hours_min, hours_max = extract_hours(raw_type)

            location = clean_text(first_by_key(job, ["formattedAddress", "locationName", "name"]))
            description = detail_text
            close_date = clean_text(first_by_key(job, ["closeDate", "postingEndDate", "endDate"]))
            posting_date = clean_text(job.get("postDate") or first_by_key(job, ["postDate", "postingDate"]))

            evidence = ""
            if min_comp not in (None, ""):
                evidence = f"ADP payGradeRange: {min_comp} - {max_comp} {currency} {period}".strip()

            row = {
                "Job Opportunity Name": title,
                "Employer Name": employer,
                "Job Opportunity Stage": "Open",
                "Close Date": close_date,
                "Source": "ADP Workforce Now",
                "Min Hours per Week": hours_min,
                "Max Hours per Week": hours_max,
                "Job Term": job_term(raw_type, title),
                "Min Wage Normalized Hourly": normalize_hourly(min_comp, period),
                "Max Wage Normalized Hourly": normalize_hourly(max_comp, period),
                "Required Education": extract_education(detail_text),
                "Required Certifications & Licenses": extract_required_certs(detail_text),
                "Category": category_from_title(title),
                "Vaccine Requirement": vaccine_requirement(detail_text),
                "Location": location,
                "Posting Date": posting_date,
                "Original Min Compensation": min_comp,
                "Original Max Compensation": max_comp,
                "Compensation Period": period,
                "Compensation Currency": currency,
                "Compensation Evidence": evidence,
                "Compensation Source": "ADP structured API" if evidence else "",
                "Job Type Raw": raw_type,
                "Job ID": job_id,
                "Job Description": description,
                "Responsibilities": "",
                "Qualifications": detail_text,
                "Job Detail URL": detail_url,
                "Source URL": search_url,
                "Date Scraped": datetime.now().isoformat(timespec="seconds"),
            }
            records.append(row)

            if save_debug:
                d = debug_root / (job_id or f"job_{idx}")
                d.mkdir(parents=True, exist_ok=True)
                (d / "list_record.json").write_text(json.dumps(job, indent=2, default=str), encoding="utf-8")
                (d / "page.txt").write_text(detail_text, encoding="utf-8")

            time.sleep(0.1)
        browser.close()

    return pd.DataFrame(records).reindex(columns=CANONICAL_COLUMNS)
