import re, time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import pandas as pd
from playwright.sync_api import sync_playwright

from common_utils import clean_text, extract_compensation, extract_education, extract_hours, extract_required_certs, category_from_title, job_term, vaccine_requirement
from schema import CANONICAL_COLUMNS


def _clientkey(url):
    m=re.search(r"/portal/([A-Za-z0-9]+)/",url)
    return m.group(1) if m else ""

def scrape_paycom(search_url: str, max_jobs: Optional[int]=None, debug_dir: str="debug", save_debug: bool=True, progress_callback: Optional[Callable]=None) -> pd.DataFrame:
    key=_clientkey(search_url)
    if not key: raise ValueError("Could not determine Paycom portal client key.")
    root=Path(debug_dir)/"paycom"; records=[]
    if save_debug: root.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True); ctx=browser.new_context(); page=ctx.new_page()
        page.goto(search_url,wait_until="domcontentloaded",timeout=120000); page.wait_for_timeout(6000)
        # Scroll a few times because Paycom can lazily render posting cards.
        last=0
        for _ in range(12):
            page.mouse.wheel(0,2500); page.wait_for_timeout(400)
            try:
                now=page.locator('a').count()
                if now==last: break
                last=now
            except Exception: pass
        links=page.locator('a').evaluate_all("els=>els.map(a=>({text:(a.innerText||'').trim(),href:a.href||''}))")
        candidates=[]; seen=set()
        for x in links:
            href=x.get('href') or ''; title=clean_text(x.get('text'))
            if re.search(rf"/portal/{re.escape(key)}/jobs/\d+",href,re.I) and href not in seen:
                seen.add(href); candidates.append((title,href))
        if max_jobs is not None: candidates=candidates[:int(max_jobs)]
        if save_debug: (root/'search_links.txt').write_text('\n'.join(f"{t}\t{h}" for t,h in candidates),encoding='utf-8')
        for i,(fallback_title,href) in enumerate(candidates,1):
            page.goto(href,wait_until='domcontentloaded',timeout=120000); page.wait_for_timeout(2500)
            text=clean_text(page.locator('body').inner_text())
            title=""
            for sel in ['h1','h2','[data-testid="job-title"]']:
                try:
                    val=clean_text(page.locator(sel).first.inner_text(timeout=800))
                    if val: title=val; break
                except Exception: pass
            title=title or fallback_title or clean_text(page.title())
            if progress_callback: progress_callback(i,len(candidates),title)
            lo,hi,period,hlo,hhi,evidence=extract_compensation(text)
            hmin,hmax=extract_hours(text); jid=re.search(r"/jobs/(\d+)",href); jid=jid.group(1) if jid else ""
            # Employer is intentionally left blank unless clearly available in the rendered page title.
            employer=""; pt=clean_text(page.title())
            if pt and title and title.lower() not in pt.lower() and pt.lower() not in {'job opportunities','loading...'}: employer=pt
            records.append({
                "Job Opportunity Name":title,"Employer Name":employer,"Job Opportunity Stage":"Open","Close Date":"","Source":"Paycom",
                "Min Hours per Week":hmin,"Max Hours per Week":hmax,"Job Term":job_term(text),"Min Wage Normalized Hourly":hlo,"Max Wage Normalized Hourly":hhi,
                "Required Education":extract_education(text),"Required Certifications & Licenses":extract_required_certs(text),"Category":category_from_title(title),"Vaccine Requirement":vaccine_requirement(text),
                "Location":"","Posting Date":"","Original Min Compensation":lo,"Original Max Compensation":hi,"Compensation Period":period,"Compensation Currency":"USD" if lo is not None else "",
                "Compensation Evidence":evidence,"Compensation Source":"Paycom rendered job page" if lo is not None else "","Job Type Raw":"","Job ID":jid,
                "Job Description":text,"Responsibilities":"","Qualifications":text,"Job Detail URL":href,"Source URL":search_url,"Date Scraped":datetime.now().isoformat(timespec='seconds')
            })
            if save_debug: (root/f"{jid or i}.txt").write_text(text,encoding='utf-8')
            time.sleep(.1)
        browser.close()
    return pd.DataFrame(records).reindex(columns=CANONICAL_COLUMNS)
