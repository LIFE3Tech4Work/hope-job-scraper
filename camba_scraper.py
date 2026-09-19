import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

from common_utils import clean_text, extract_compensation, extract_education, extract_hours, extract_required_certs, category_from_title, job_term, vaccine_requirement
from schema import CANONICAL_COLUMNS


def _label_value(text: str, label: str) -> str:
    m = re.search(rf"\b{re.escape(label)}\s*:\s*(.+?)(?=\s+(?:What Does|Education|Skills|Pre and/or Post|Compensation|Status|CAMBA is|$))", text, re.I)
    return clean_text(m.group(1)) if m else ""


def scrape_camba(search_url: str, max_jobs: Optional[int]=None, debug_dir: str="debug", save_debug: bool=True, progress_callback: Optional[Callable]=None) -> pd.DataFrame:
    s=requests.Session(); s.headers.update({"User-Agent":"Mozilla/5.0"})
    r=s.get(search_url,timeout=60); r.raise_for_status(); soup=BeautifulSoup(r.text,"html.parser")
    candidates=[]; seen=set()
    for a in soup.select('a[href*="/career/"]'):
        href=urljoin(search_url,a.get("href") or ""); title=clean_text(a.get_text(" ",strip=True))
        if not href or not title or href in seen: continue
        seen.add(href); candidates.append((title,href))
    if max_jobs is not None: candidates=candidates[:int(max_jobs)]
    records=[]; root=Path(debug_dir)/"camba"
    if save_debug: root.mkdir(parents=True,exist_ok=True)
    for i,(fallback_title,href) in enumerate(candidates,1):
        if progress_callback: progress_callback(i,len(candidates),fallback_title)
        rr=s.get(href,timeout=60); rr.raise_for_status(); ds=BeautifulSoup(rr.text,"html.parser")
        title=clean_text(ds.find("h1").get_text(" ",strip=True) if ds.find("h1") else fallback_title)
        text=clean_text(ds.get_text(" ",strip=True))
        status_match=re.search(r"\bStatus\s*:\s*(.+?)(?=\s+CAMBA is|$)",text,re.I)
        status=clean_text(status_match.group(1)) if status_match else ""
        loc_match=re.search(r"\bLocation\s*:\s*(.+?)(?=\s+What Does|\s+Education|\s+Skills|$)",text,re.I)
        location=clean_text(loc_match.group(1)) if loc_match else ""
        comp_match=re.search(r"\bCompensation\s*:\s*(.+?)(?=\s+Status\s*:|$)",text,re.I)
        comp_text=clean_text(comp_match.group(1)) if comp_match else text
        lo,hi,period,hlo,hhi,evidence=extract_compensation(comp_text)
        hmin,hmax=extract_hours(status+" "+text)
        slug=urlparse(href).path.rstrip('/').split('/')[-1]
        records.append({
            "Job Opportunity Name":title,"Employer Name":"CAMBA, Inc.","Job Opportunity Stage":"Open","Close Date":"","Source":"CAMBA Careers",
            "Min Hours per Week":hmin,"Max Hours per Week":hmax,"Job Term":job_term(status+" "+title),"Min Wage Normalized Hourly":hlo,"Max Wage Normalized Hourly":hhi,
            "Required Education":extract_education(text),"Required Certifications & Licenses":extract_required_certs(text),"Category":category_from_title(title),"Vaccine Requirement":vaccine_requirement(text),
            "Location":location,"Posting Date":"","Original Min Compensation":lo,"Original Max Compensation":hi,"Compensation Period":period,"Compensation Currency":"USD" if lo is not None else "",
            "Compensation Evidence":evidence,"Compensation Source":"CAMBA job page" if lo is not None else "","Job Type Raw":status,"Job ID":slug,
            "Job Description":text,"Responsibilities":"","Qualifications":text,"Job Detail URL":href,"Source URL":search_url,"Date Scraped":datetime.now().isoformat(timespec="seconds")
        })
        if save_debug: (root/f"{slug}.txt").write_text(text,encoding="utf-8")
    return pd.DataFrame(records).reindex(columns=CANONICAL_COLUMNS)
