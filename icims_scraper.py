import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

from common_utils import clean_text, normalize_hourly, extract_education, extract_hours, extract_required_certs, category_from_title, job_term, vaccine_requirement
from schema import CANONICAL_COLUMNS


def _strings(soup): return [clean_text(x) for x in soup.stripped_strings if clean_text(x)]

def _after(lines,label):
    for i,x in enumerate(lines):
        if x.lower()==label.lower() and i+1<len(lines): return lines[i+1]
    return ""

def _money_value(s):
    m=re.search(r"\$\s*([0-9][0-9,]*(?:\.\d+)?)",s or "")
    return float(m.group(1).replace(',','')) if m else None

def scrape_icims(search_url: str, max_jobs: Optional[int]=None, debug_dir: str="debug", save_debug: bool=True, progress_callback: Optional[Callable]=None) -> pd.DataFrame:
    s=requests.Session(); s.headers.update({"User-Agent":"Mozilla/5.0"})
    detail_urls=[]; seen=set(); current=search_url; pages=0
    while current and pages<200:
        pages+=1; r=s.get(current,timeout=60); r.raise_for_status(); soup=BeautifulSoup(r.text,"html.parser")
        for a in soup.select('a[href*="/jobs/"]'):
            href=urljoin(current,a.get("href") or "")
            if re.search(r"/jobs/\d+/.+?/job/?(?:\?|$)",href) and href not in seen:
                seen.add(href); detail_urls.append(href)
                if max_jobs is not None and len(detail_urls)>=int(max_jobs): break
        if max_jobs is not None and len(detail_urls)>=int(max_jobs): break
        nxt=None
        for a in soup.find_all('a',href=True):
            txt=clean_text(a.get_text(" ",strip=True)).lower()
            aria=clean_text(a.get('aria-label')).lower()
            if "next page" in txt or "next page" in aria or txt=="next":
                candidate=urljoin(current,a['href'])
                if candidate!=current: nxt=candidate; break
        current=nxt
        if not current: break
    if max_jobs is not None: detail_urls=detail_urls[:int(max_jobs)]
    root=Path(debug_dir)/"icims"; records=[]
    if save_debug: root.mkdir(parents=True,exist_ok=True)
    for i,href in enumerate(detail_urls,1):
        rr=s.get(href,timeout=60); rr.raise_for_status(); soup=BeautifulSoup(rr.text,"html.parser"); lines=_strings(soup); text=" ".join(lines)
        title=clean_text(soup.find("h1").get_text(" ",strip=True) if soup.find("h1") else "")
        if progress_callback: progress_callback(i,len(detail_urls),title or href)
        location=_after(lines,"Job Location"); category=_after(lines,"Category") or category_from_title(title); raw_type=_after(lines,"Type")
        min_s=_after(lines,"Min"); max_s=_after(lines,"Max"); lo=_money_value(min_s); hi=_money_value(max_s)
        period="Hourly" if any("/hr" in x.lower() for x in [min_s,max_s]) else ("Annually" if any(x in (min_s+max_s).lower() for x in ["/yr","/year"]) else "")
        hlo=normalize_hourly(lo,period) if lo is not None else None; hhi=normalize_hourly(hi,period) if hi is not None else None
        job_id=_after(lines,"ID") or (re.search(r"/jobs/(\d+)/",href).group(1) if re.search(r"/jobs/(\d+)/",href) else "")
        evidence="; ".join(x for x in [f"Min {min_s}" if min_s else "",f"Max {max_s}" if max_s else ""] if x)
        quals=text[text.lower().find("qualifications"):] if "qualifications" in text.lower() else text
        records.append({
            "Job Opportunity Name":title,"Employer Name":"Services for the Underserved (S:US)","Job Opportunity Stage":"Open","Close Date":"","Source":"iCIMS",
            "Min Hours per Week":extract_hours(text)[0],"Max Hours per Week":extract_hours(text)[1],"Job Term":job_term(raw_type),"Min Wage Normalized Hourly":hlo,"Max Wage Normalized Hourly":hhi,
            "Required Education":extract_education(quals),"Required Certifications & Licenses":extract_required_certs(quals),"Category":category,"Vaccine Requirement":vaccine_requirement(text),
            "Location":location,"Posting Date":"","Original Min Compensation":lo,"Original Max Compensation":hi,"Compensation Period":period,"Compensation Currency":"USD" if lo is not None else "",
            "Compensation Evidence":evidence,"Compensation Source":"iCIMS job page" if lo is not None else "","Job Type Raw":raw_type,"Job ID":job_id,
            "Job Description":text,"Responsibilities":"","Qualifications":quals,"Job Detail URL":href,"Source URL":search_url,"Date Scraped":datetime.now().isoformat(timespec="seconds")
        })
        if save_debug: (root/f"{job_id or i}.txt").write_text(text,encoding="utf-8")
    return pd.DataFrame(records).reindex(columns=CANONICAL_COLUMNS)
