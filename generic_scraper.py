"""Configurable browser fallback. Partial coverage is explicitly reported."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
from playwright.sync_api import sync_playwright
from ats_bridge import board_config
from common_utils import clean_text
from schema import CANONICAL_COLUMNS


def job_nodes(value):
    if isinstance(value, list):
        for child in value: yield from job_nodes(child)
    elif isinstance(value, dict):
        kind=value.get('@type',[])
        if kind=='JobPosting' or isinstance(kind,list) and 'JobPosting' in kind: yield value
        for key in ('@graph','mainEntity','itemListElement','item'):
            if key in value: yield from job_nodes(value[key])


def scrape_generic(search_url, max_jobs=5, debug_dir='debug', save_debug=True, progress_callback=None, ocr=None):
    cfg=board_config(search_url).get('browser',{})
    limit=int(max_jobs) if max_jobs is not None else None
    if limit is not None and limit<1: raise ValueError('max_jobs must be positive')
    max_pages=int(cfg.get('max_pages',10));candidates=[];seen=set();records=[];errors=[];ocr_audits=[]
    debug=Path(debug_dir)/('generic_'+hashlib.sha256(search_url.encode()).hexdigest()[:12])
    if save_debug: debug.mkdir(parents=True,exist_ok=True)
    host=urlparse(search_url).hostname
    def navigate(page,url):
        response=page.goto(url,wait_until='domcontentloaded',timeout=int(cfg.get('timeout_ms',60000)))
        if response and response.status>=400: raise RuntimeError(f'HTTP {response.status}: {url}')
        page.wait_for_timeout(int(cfg.get('wait_ms',1500)))
        if cfg.get('wait_selector'): page.locator(cfg['wait_selector']).first.wait_for(timeout=15000)
    def evidence(page,label):
        if save_debug:
            (debug/f'{label}.html').write_text(page.content(),encoding='utf-8')
            page.screenshot(path=str(debug/f'{label}.png'),full_page=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=cfg.get('headless',True));page=browser.new_page()
        try:
            navigate(page,search_url)
            for page_number in range(max_pages):
                evidence(page,f'listing_{page_number+1}')
                contexts=page.frames if cfg.get('include_iframes',False) else [page]
                for context in contexts:
                    links=context.locator(cfg.get('job_link_selector','a[href]')).evaluate_all("els => els.map(a => ({text:(a.innerText||'').trim(), href:a.href||''}))")
                    for item in links:
                        title=clean_text(item.get('text'));url=item.get('href','');parsed=urlparse(url)
                        if parsed.scheme not in {'http','https'} or url in seen: continue
                        if parsed.hostname!=host and not cfg.get('allow_external_links',False): continue
                        if not cfg.get('job_link_selector') and not any(x in parsed.path.lower() for x in ('job','career','requisition','position','opening')): continue
                        if not 3<=len(title)<=180: continue
                        seen.add(url);candidates.append((title,url))
                if limit is not None and len(candidates)>=limit: break
                selector=cfg.get('next_page_selector')
                if not selector: break
                nxt=page.locator(selector).first
                if not nxt.count() or not nxt.is_visible() or not nxt.is_enabled() or nxt.get_attribute('aria-disabled')=='true': break
                nxt.click();page.wait_for_timeout(int(cfg.get('wait_ms',1500)))
            # Direct posting input is supported if the page exposes JobPosting data.
            if not candidates: candidates=[('',search_url)]
            for i,(title,url) in enumerate(candidates,1):
                if limit is not None and len(records)>=limit: break
                if progress_callback: progress_callback(i,len(candidates),title or url)
                try:
                    navigate(page,url);evidence(page,f'job_{i}')
                    nodes=[]
                    for content in page.locator('script[type="application/ld+json"]').all_text_contents():
                        try: nodes.extend(job_nodes(json.loads(content)))
                        except (ValueError,TypeError): pass
                    if nodes:
                        # A result/listing page containing multiple postings is not a job detail page.
                        if len(nodes)!=1: errors.append({'url':url,'reason':'Multiple JobPosting nodes; configure detail links'});continue
                        node=nodes[0];title=clean_text(node.get('title'));desc=clean_text(node.get('description'))
                        org=node.get('hiringOrganization') or {};employer=org.get('name','') if isinstance(org,dict) else ''
                    elif cfg.get('description_selector'):
                        node={};desc=clean_text(page.locator(cfg['description_selector']).first.inner_text());employer=cfg.get('employer_name','')
                        if cfg.get('title_selector'): title=clean_text(page.locator(cfg['title_selector']).first.inner_text())
                    else:
                        node={};desc='';employer=''
                    if not title or not desc:
                        if ocr and ocr.enabled:
                            from ocr_fallback import extract_page
                            row,audit=extract_page(page,url,search_url,ocr,debug_dir,title_hint=title)
                            records.append(row);ocr_audits.append(audit)
                        else: errors.append({'url':url,'reason':'Missing supported title or description; screenshot extraction disabled'})
                        continue
                    row={key:None for key in CANONICAL_COLUMNS}
                    row.update({'Job Opportunity Name':title,'Employer Name':employer,'Source':'Generic browser - review required','Source URL':search_url,'Job Detail URL':url,'Job Description':desc,'Posting Date':node.get('datePosted'),'Close Date':node.get('validThrough'),'Job Type Raw':str(node.get('employmentType') or ''),'Vaccine Requirement':'Not Stated','Date Scraped':datetime.now(timezone.utc).isoformat(timespec='seconds')})
                    # Generic results deliberately avoid guessing stage, compensation or mandatory credentials.
                    records.append(row)
                except Exception as exc: errors.append({'url':url,'reason':str(exc)})
        finally: browser.close()
    df=pd.DataFrame(records).reindex(columns=CANONICAL_COLUMNS)
    if len(df): df=df.drop_duplicates('Job Detail URL').reset_index(drop=True)
    df.attrs['warnings']=['Browser fallback coverage is unverified; review extracted rows. No next-page selector means only discovered links on the first listing page are scanned.',f'Scanned up to {max_pages} listing pages; {len(errors)} detail failures/rejections.']
    df.attrs['ocr_audits']=ocr_audits
    if ocr_audits: df.attrs['warnings'].append('Local screenshot OCR rows require human review; evidence is saved in debug/ocr.')
    if save_debug: (debug/'diagnostics.json').write_text(json.dumps({'warnings':df.attrs['warnings'],'errors':errors},indent=2))
    if df.empty: raise RuntimeError('No verified job records. Configure board selectors or an ATS override; inspect debug evidence if enabled. ' + ('Last error: '+errors[-1]['reason'] if errors else ''))
    return df
