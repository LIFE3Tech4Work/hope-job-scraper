import re, time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import pandas as pd
from playwright.sync_api import sync_playwright

from common_utils import clean_text, extract_compensation, extract_education, extract_hours, extract_required_certs, category_from_title, job_term, vaccine_requirement
from schema import CANONICAL_COLUMNS

EXCLUDE=('newsletter','training','directions','privacy','board of directors','register','contact','instagram','facebook','linkedin','jobs at bnydc')

def scrape_brooklyn_navy_yard(search_url: str, max_jobs: Optional[int]=None, debug_dir: str='debug', save_debug: bool=True, progress_callback: Optional[Callable]=None) -> pd.DataFrame:
    root=Path(debug_dir)/'brooklyn_navy_yard'; records=[]
    if save_debug: root.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True); ctx=browser.new_context(); page=ctx.new_page()
        page.goto(search_url,wait_until='domcontentloaded',timeout=120000); page.wait_for_timeout(6000)
        candidates=[]; seen=set()
        for frame in page.frames:
            try:
                links=frame.locator('a').evaluate_all("els=>els.map(a=>({text:(a.innerText||'').trim(),href:a.href||''}))")
            except Exception: continue
            for x in links:
                title=clean_text(x.get('text')); href=x.get('href') or ''
                low=(title+' '+href).lower()
                if not title or not href or href in seen or any(k in low for k in EXCLUDE): continue
                # Keep likely posting/apply links, including external employer ATS links.
                if not (re.search(r'\b(job|jobs|career|position|apply|opening)\b',low) or re.search(r'/jobs?/\d+',href,re.I)): continue
                if len(title)>180: continue
                seen.add(href); candidates.append((title,href))
        if max_jobs is not None: candidates=candidates[:int(max_jobs)]
        if save_debug:
            (root/'discovered_links.txt').write_text('\n'.join(f"{t}\t{h}" for t,h in candidates),encoding='utf-8')
            try: (root/'search_page.txt').write_text(clean_text(page.locator('body').inner_text()),encoding='utf-8')
            except Exception: pass
        for i,(fallback_title,href) in enumerate(candidates,1):
            try:
                page.goto(href,wait_until='domcontentloaded',timeout=120000); page.wait_for_timeout(1800); text=clean_text(page.locator('body').inner_text()); pt=clean_text(page.title())
            except Exception:
                text=''; pt=''
            title=fallback_title
            try:
                h1=clean_text(page.locator('h1').first.inner_text(timeout=600)); title=h1 or title
            except Exception: pass
            if progress_callback: progress_callback(i,len(candidates),title)
            lo,hi,period,hlo,hhi,evidence=extract_compensation(text); hmin,hmax=extract_hours(text)
            records.append({
                "Job Opportunity Name":title,"Employer Name":"","Job Opportunity Stage":"Open","Close Date":"","Source":"Brooklyn Navy Yard aggregator",
                "Min Hours per Week":hmin,"Max Hours per Week":hmax,"Job Term":job_term(text),"Min Wage Normalized Hourly":hlo,"Max Wage Normalized Hourly":hhi,
                "Required Education":extract_education(text),"Required Certifications & Licenses":extract_required_certs(text),"Category":category_from_title(title),"Vaccine Requirement":vaccine_requirement(text),
                "Location":"Brooklyn Navy Yard, Brooklyn, NY","Posting Date":"","Original Min Compensation":lo,"Original Max Compensation":hi,"Compensation Period":period,"Compensation Currency":"USD" if lo is not None else "",
                "Compensation Evidence":evidence,"Compensation Source":"Linked employer job page" if lo is not None else "","Job Type Raw":"","Job ID":"",
                "Job Description":text,"Responsibilities":"","Qualifications":text,"Job Detail URL":href,"Source URL":search_url,"Date Scraped":datetime.now().isoformat(timespec='seconds')
            })
            if save_debug: (root/f'job_{i}.txt').write_text(text,encoding='utf-8')
            time.sleep(.1)
        browser.close()
    return pd.DataFrame(records).reindex(columns=CANONICAL_COLUMNS)
