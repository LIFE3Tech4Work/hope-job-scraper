import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import pandas as pd
import requests

from common_utils import clean_text, extract_compensation, extract_education, extract_hours, extract_required_certs, category_from_title, job_term, vaccine_requirement, json_text
from schema import CANONICAL_COLUMNS

LIST_URL = "https://my.adp.com/myadp_prefix/mycareer/public/staffing/v1/job-requisitions/apply-custom-filters"
DETAIL_PREFIX = "https://my.adp.com/myadp_prefix/mycareer/public/staffing/v1/job-requisitions/search-meta/"
CONFIG_PREFIX = "https://myjobs.adp.com/public/staffing/v1/career-site/"
SELECT = "reqId,jobTitle,publishedJobTitle,type,jobDescription,jobQualifications,workLevelCode,clientRequisitionID,postingDate,requisitionLocations"


def _slug(url: str) -> str:
    parts = [p for p in urlparse(url).path.split("/") if p]
    if not parts:
        raise ValueError("Could not determine ADP MyJobs tenant slug.")
    return parts[0].lower()


def _headers(token: str, orgoid: str):
    h = {
        "Accept": "application/json",
        "Origin": "https://myjobs.adp.com",
        "Referer": "https://myjobs.adp.com/",
        "myjobstoken": token,
        "rolecode": "manager",
        "Accept-Language": "en-US",
        "User-Agent": "Mozilla/5.0",
    }
    if orgoid:
        h["orgoid"] = orgoid
    return h


def _primary_location(job):
    locs = job.get("requisitionLocations") or []
    if not isinstance(locs, list): return ""
    loc = next((x for x in locs if x.get("primaryIndicator")), locs[0] if locs else {})
    addr = loc.get("address") or {}
    name = (loc.get("nameCode") or {}).get("shortName") or (loc.get("nameCode") or {}).get("longName")
    if name: return clean_text(name)
    parts = [addr.get("cityName"), (addr.get("countrySubdivisionLevel1") or {}).get("shortName"), addr.get("postalCode")]
    return ", ".join(clean_text(x) for x in parts if clean_text(x))


def scrape_adp_myjobs(search_url: str, max_jobs: Optional[int]=None, debug_dir: str="debug", save_debug: bool=True, progress_callback: Optional[Callable]=None) -> pd.DataFrame:
    slug = _slug(search_url)
    session = requests.Session()
    session.headers.update({"User-Agent":"Mozilla/5.0"})
    config_r = session.get(CONFIG_PREFIX + slug, timeout=30, headers={"Accept":"application/json"})
    if config_r.status_code != 200:
        raise RuntimeError(f"ADP MyJobs career-site config failed with HTTP {config_r.status_code}")
    config = config_r.json()
    token = config.get("myJobsToken") or ""
    orgoid = config.get("orgoid") or ""
    employer = clean_text(config.get("clientName") or config.get("name") or slug)
    if not token:
        raise RuntimeError("ADP MyJobs did not return a public myJobsToken.")
    headers = _headers(token, orgoid)

    jobs = []
    skip = 0
    page_size = 100
    total = None
    while True:
        top = page_size
        if max_jobs is not None:
            remaining = int(max_jobs) - len(jobs)
            if remaining <= 0: break
            top = min(top, remaining)
        params = {"$orderby":"postingDate desc", "$select":SELECT, "$top":top, "$skip":skip, "tz":"America/New_York"}
        r = session.get(LIST_URL, params=params, headers=headers, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"ADP MyJobs listing API failed with HTTP {r.status_code}")
        payload = r.json()
        page_jobs = payload.get("jobRequisitions") or []
        if total is None: total = payload.get("count")
        jobs.extend(page_jobs)
        if not page_jobs or (total is not None and len(jobs) >= int(total)): break
        skip += len(page_jobs)
        time.sleep(0.25)

    if max_jobs is not None: jobs = jobs[:int(max_jobs)]
    debug_root = Path(debug_dir)/"adp_myjobs"
    if save_debug:
        debug_root.mkdir(parents=True, exist_ok=True)
        (debug_root/"career_site.json").write_text(json.dumps(config,indent=2,default=str),encoding="utf-8")

    records=[]
    n=len(jobs)
    for i, item in enumerate(jobs,1):
        req_id = clean_text(item.get("reqId") or item.get("clientRequisitionID"))
        title = clean_text(item.get("publishedJobTitle") or item.get("jobTitle"))
        if progress_callback: progress_callback(i,n,title)
        detail = item
        if req_id:
            rr = session.get(DETAIL_PREFIX + req_id, headers=headers, timeout=60)
            if rr.status_code == 200:
                raw = rr.json()
                dlist = raw.get("jobRequisitions") or []
                if dlist: detail = {**item, **dlist[0]}
            else:
                raw = {"http_status":rr.status_code}
        else:
            raw = {}
        desc = clean_text(detail.get("jobDescription") or item.get("jobDescription"))
        quals = clean_text(detail.get("jobQualifications") or item.get("jobQualifications"))
        all_text = " ".join([title, desc, quals, json_text(detail)])
        lo,hi,period,hlo,hhi,evidence = extract_compensation(all_text)
        raw_type = clean_text(detail.get("workLevelCode") or detail.get("type") or item.get("workLevelCode") or item.get("type"))
        hmin,hmax = extract_hours(raw_type + " " + all_text)
        location = _primary_location(detail) or _primary_location(item)
        job_url = f"https://myjobs.adp.com/{slug}/cx/job/{req_id}" if req_id else search_url
        records.append({
            "Job Opportunity Name":title,"Employer Name":employer,"Job Opportunity Stage":"Open","Close Date":"","Source":"ADP MyJobs",
            "Min Hours per Week":hmin,"Max Hours per Week":hmax,"Job Term":job_term(raw_type+" "+title),
            "Min Wage Normalized Hourly":hlo,"Max Wage Normalized Hourly":hhi,"Required Education":extract_education(quals),
            "Required Certifications & Licenses":extract_required_certs(quals),"Category":category_from_title(title),"Vaccine Requirement":vaccine_requirement(all_text),
            "Location":location,"Posting Date":clean_text(detail.get("postingDate") or item.get("postingDate")),
            "Original Min Compensation":lo,"Original Max Compensation":hi,"Compensation Period":period,"Compensation Currency":"USD" if lo is not None else "",
            "Compensation Evidence":evidence,"Compensation Source":"ADP MyJobs public API/detail" if lo is not None else "",
            "Job Type Raw":raw_type,"Job ID":req_id,"Job Description":desc,"Responsibilities":"","Qualifications":quals,
            "Job Detail URL":job_url,"Source URL":search_url,"Date Scraped":datetime.now().isoformat(timespec="seconds")
        })
        if save_debug:
            d=debug_root/(req_id or f"job_{i}"); d.mkdir(parents=True,exist_ok=True)
            (d/"detail.json").write_text(json.dumps(detail,indent=2,default=str),encoding="utf-8")
    return pd.DataFrame(records).reindex(columns=CANONICAL_COLUMNS)
