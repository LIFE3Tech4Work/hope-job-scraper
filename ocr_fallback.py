"""Local screenshot OCR using Tesseract. No hosted model or API requests."""
import csv
import hashlib
import io
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from schema import CANONICAL_COLUMNS
from common_utils import normalize_hourly

@dataclass
class OCRSettings:
    enabled: bool = False
    max_postings: int = 5
    max_screenshots: int = 6
    postings_used: int = 0
    language: str = 'eng'
    min_confidence: float = 65
    tesseract_cmd: str = 'tesseract'

    def validate(self):
        if not self.enabled: raise RuntimeError('Local screenshot OCR is disabled')
        if not shutil.which(self.tesseract_cmd): raise RuntimeError('Install Tesseract first: brew install tesseract (macOS)')
        if not 1<=self.max_postings<=100 or not 1<=self.max_screenshots<=12: raise ValueError('Invalid OCR limits')


def read_image(path, settings):
    """Retain TSV evidence; discard entire low-confidence lines, not just words."""
    settings.validate()
    proc=subprocess.run([settings.tesseract_cmd,str(path),'stdout','-l',settings.language,'--psm','3','tsv'],capture_output=True,text=True,timeout=45)
    if proc.returncode: raise RuntimeError('Local Tesseract failed: '+proc.stderr[-500:])
    grouped={}
    for w in csv.DictReader(io.StringIO(proc.stdout),delimiter='\t'):
        if w.get('level')!='5' or not w.get('text','').strip():continue
        grouped.setdefault(tuple(w[k] for k in ['page_num','block_num','par_num','line_num']),[]).append(w)
    lines=[]
    for words in grouped.values():
        confidence=min(float(w['conf']) for w in words)
        lines.append({'text':' '.join(w['text'] for w in words),'confidence':confidence,'accepted':confidence>=settings.min_confidence,'image':str(path)})
    return lines,proc.stdout


def parse_fields(lines, url, source_url, title_hint='', verified_posting=False):
    """Conservative rules. Evidence is retained for every inferred field."""
    accepted=[x for x in lines if x['accepted']];text='\n'.join(x['text'] for x in accepted)
    if re.search(r'verify (?:you are|you.re) human|access denied|captcha|sign in to (?:view|continue)|checking your browser',text,re.I):
        raise ValueError('Blocked or sign-in page; no job fields accepted')
    if not verified_posting and re.search(r'search results|\d+ jobs found|filter jobs|job alerts',text,re.I):
        raise ValueError('Listing page detected; provide a single job posting URL')
    row={k:None for k in CANONICAL_COLUMNS};evidence={}
    def set_field(key,value,line):
        if value is not None and value!='':
            row[key]=value;evidence[key]={'quote':line['text'],'image':line['image'],'confidence':line['confidence']}
    labels={'Job Opportunity Name':r'(?:job title|position title|position)', 'Employer Name':r'(?:employer|company)', 'Location':r'(?:job location|location)', 'Posting Date':r'(?:date posted|posted on|posting date)', 'Close Date':r'(?:closing date|application deadline|apply by)', 'Job ID':r'(?:job id|requisition id|requisition number)', 'Job Type Raw':r'(?:employment type|job type)', 'Category':r'(?:job category)'}
    for line in accepted:
        s=line['text']
        for field,label in labels.items():
            m=re.match(r'^\s*'+label+r'\s*:\s*(.+)$',s,re.I)
            if m and not row[field]:set_field(field,m.group(1).strip(),line)
    # Match an already-known title to pixels. The hint itself is not evidence.
    if not row['Job Opportunity Name'] and title_hint:
        hint=re.sub(r'\W+','',title_hint).lower()
        for line in accepted:
            if hint and hint in re.sub(r'\W+','',line['text']).lower():set_field('Job Opportunity Name',title_hint,line);break
    # Common unlabeled title fallback, limited to short occupational headings.
    if not row['Job Opportunity Name']:
        for line in accepted[:18]:
            s=line['text']
            if len(s)<90 and len(s.split())<=10 and re.search(r'\b(technician|installer|engineer|porter|gardener|manager|coordinator|assistant|specialist|operator|mechanic|analyst|auditor|electrician|plumber|supervisor|associate)\b',s,re.I) and not re.search(r'\b(hiring|search|find|apply|looking|seeking|experience)\b',s,re.I):
                set_field('Job Opportunity Name',s,line);break
    if not row['Job Opportunity Name'] or not re.search(r'responsibilit|qualification|job description|requirements|duties',text,re.I):
        raise ValueError('OCR could not verify a job title and description; review screenshots')
    # Prefer an explicit description section; never create one from absent text.
    start=re.search(r'(?:job description|responsibilities|duties)\s*:?\s*',text,re.I)
    desc=text[start.start():] if start else text
    row['Job Description']=desc;evidence['Job Description']={'text_file':'ocr_text.txt','note':'Visible accepted OCR lines only; review page navigation and related content.'}
    for line in accepted:
        s=line['text'];low=s.lower()
        hours=re.search(r'(\d+(?:\.\d+)?)(?:\s*(?:-|–|to)\s*(\d+(?:\.\d+)?))?\s*(?:hours?|hrs?)\s*(?:per week|/week|weekly)',s,re.I)
        if hours and not row['Min Hours per Week']:
            lo=float(hours.group(1));hi=float(hours.group(2) or hours.group(1))
            if 0<lo<=hi<=168:set_field('Min Hours per Week',lo,line);set_field('Max Hours per Week',hi,line)
        # Explicit pay unit and amount on the same line; no magnitude assumptions.
        salary=re.search(r'(?:USD\s*|CAD\s*|EUR\s*|GBP\s*)?[$€£]\s*([\d,]+(?:\.\d{1,2})?)(?:\s*(?:-|–|—|to)\s*[$€£]?\s*([\d,]+(?:\.\d{1,2})?))?\s*(?:per\s+|/\s*)?(hour(?:ly)?|hr|year(?:ly)?|annually|annual|week(?:ly)?|month(?:ly)?|day|daily)\b',s,re.I)
        if salary and not re.search(r'bonus|reimbursement|allowance',low) and row['Original Min Compensation'] is None:
            lo=float(salary.group(1).replace(',',''));hi=float((salary.group(2) or salary.group(1)).replace(',',''))
            unit=salary.group(3).lower();period=next((v for keys,v in [(['hour','hr'],'Hourly'),(['year','annual'],'Annually'),(['week'],'Weekly'),(['month'],'Monthly'),(['day','daily'],'Daily')] if any(unit.startswith(k) for k in keys)),None)
            if 0<lo<=hi and period:
                set_field('Original Min Compensation',lo,line);set_field('Original Max Compensation',hi,line);set_field('Compensation Period',period,line)
                currency=re.search(r'\b(USD|CAD|EUR|GBP|AUD)\b',s)
                if currency:set_field('Compensation Currency',currency.group(1),line)
                elif '€' in s:set_field('Compensation Currency','EUR',line)
                elif '£' in s:set_field('Compensation Currency','GBP',line)
                row['Min Wage Normalized Hourly']=normalize_hourly(lo,period);row['Max Wage Normalized Hourly']=normalize_hourly(hi,period)
                row['Compensation Evidence']=s;row['Compensation Source']='Local screenshot OCR - review required'
        if re.search(r'\brequired\b|\bmust (?:have|hold|possess)\b',low) and not re.search(r'preferred|not required|no .{0,25}required',low):
            if re.search(r'diploma|\bged\b|degree',low):set_field('Required Education',s,line)
            if re.search(r'licen[sc]e|certificat|\bepa\b|\bosha\b|\bcdl\b',low):set_field('Required Certifications & Licenses',s,line)
        if re.search(r'vaccination not required|vaccine not required',low):set_field('Vaccine Requirement','No',line)
        elif re.search(r'vaccination required|vaccine required|must be vaccinated',low):set_field('Vaccine Requirement','Yes',line)
        m=re.match(r'\s*(?:job term|employment term|contract type)\s*:\s*(permanent|temporary|contract)\b',s,re.I)
        if m:set_field('Job Term',m.group(1).title(),line)
        m=re.match(r'\s*(?:job status|posting status)\s*:\s*(open|closed)\b',s,re.I)
        if m:set_field('Job Opportunity Stage',m.group(1).title(),line)
    row.update({'Source':'Local screenshot OCR - review required','Source URL':source_url,'Job Detail URL':url,'Date Scraped':datetime.now(timezone.utc).isoformat(timespec='seconds')})
    return row,evidence


def extract_page(page,url,source_url,settings,debug_dir='debug',title_hint='',verified_posting=False):
    settings.validate()
    if settings.postings_used>=settings.max_postings:raise RuntimeError('Local OCR posting limit reached for this run')
    settings.postings_used+=1
    run=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    folder=Path(debug_dir)/'ocr'/f'{hashlib.sha256(url.encode()).hexdigest()[:12]}_{run}';folder.mkdir(parents=True,exist_ok=True)
    page.set_viewport_size({'width':1440,'height':1000})
    paths=[];last_y=-1;truncated=False;lines=[]
    try:
        for i in range(settings.max_screenshots):
            page.evaluate('(y)=>window.scrollTo(0,y)',i*800);page.wait_for_timeout(300)
            y=page.evaluate('window.scrollY')
            if i and y==last_y:break
            last_y=y;path=folder/f'viewport_{i+1:02}.png';page.screenshot(path=str(path),full_page=False);paths.append(path)
            part,tsv=read_image(path,settings);lines.extend(part);path.with_suffix('.tsv').write_text(tsv,encoding='utf-8')
            if page.evaluate('window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2'):break
        else:truncated=True
    finally:page.evaluate('window.scrollTo(0,0)')
    # Overlapping captures repeat lines; keep the first occurrence as evidence.
    seen=set();lines=[x for x in lines if not (x['text'] in seen or seen.add(x['text']))]
    (folder/'ocr_text.txt').write_text('\n'.join(x['text'] for x in lines if x['accepted']),encoding='utf-8')
    audit={'url':url,'source_url':source_url,'screenshots':[str(p) for p in paths],'capture_limit_reached':truncated,'review_required':True,'lines':lines}
    audit_path=folder/'extraction.json'
    try:
        row,evidence=parse_fields(lines,url,source_url,title_hint,verified_posting);audit.update({'fields':row,'field_evidence':evidence})
    except Exception as exc:
        audit['error']=str(exc);raise
    finally:audit_path.write_text(json.dumps(audit,indent=2),encoding='utf-8')
    return row,{'audit_file':str(audit_path),'screenshots':[str(p) for p in paths],'capture_limit_reached':truncated,'review_required':True}


def scrape_posting_screenshot(url,settings,debug_dir='debug',title_hint='',verified_posting=False):
    from playwright.sync_api import sync_playwright
    import pandas as pd
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page();response=page.goto(url,wait_until='domcontentloaded',timeout=60000)
            if response and response.status>=400:raise RuntimeError(f'Posting returned HTTP {response.status}')
            page.wait_for_timeout(2000)
            row,audit=extract_page(page,url,url,settings,debug_dir,title_hint,verified_posting)
        finally:browser.close()
    df=pd.DataFrame([row]).reindex(columns=CANONICAL_COLUMNS)
    df.attrs['warnings']=['Local screenshot OCR requires review. Evidence: '+audit['audit_file']]
    df.attrs['ocr_audits']=[audit]
    return df
