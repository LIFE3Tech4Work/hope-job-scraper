"""Optional integration with the pinned kalil0321/ats-scrapers repository."""
import importlib.util
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from common_utils import clean_text, normalize_hourly
from schema import CANONICAL_COLUMNS

CONFIG_PATH = Path(__file__).with_name('board_configs.json')


def board_config(url):
    data = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    # Exact URLs prevent one employer's settings leaking to another tenant.
    return dict(data.get('boards', {}).get(url, {}))


def available():
    return importlib.util.find_spec('ats_scrapers') is not None


def resolve(url):
    if not available():
        return None
    from ats_scrapers import resolve_careers_url
    return resolve_careers_url(url)


def normalize_job(job, source_url):
    d = job.model_dump(mode='json') if hasattr(job, 'model_dump') else job
    row = {key: None for key in CANONICAL_COLUMNS}
    period = {'HOUR':'Hourly','DAY':'Daily','WEEK':'Weekly','MONTH':'Monthly','YEAR':'Annually'}.get(d.get('salary_period'), '')
    lo, hi = d.get('salary_min'), d.get('salary_max')
    desc = clean_text(d.get('description'))
    # Preserve upstream evidence; never turn full-time into permanent employment,
    # infer mandatory credentials from mentions, or assume missing compensation.
    row.update({
        'Job Opportunity Name': clean_text(d.get('title')),
        'Employer Name': clean_text(d.get('company')),
        'Source': 'ats-scrapers / ' + str(d.get('ats_type', '')),
        'Source URL': source_url,
        'Job Detail URL': str(d.get('url') or d.get('apply_url') or ''),
        'Job ID': d.get('ats_id'), 'Job Description': desc,
        'Location': d.get('location'), 'Posting Date': d.get('posted_at'),
        'Close Date': d.get('application_deadline'),
        'Job Type Raw': d.get('commitment') or d.get('employment_type'),
        'Job Term': {'CONTRACT':'Contract','TEMPORARY':'Temporary','INTERN':'Temporary'}.get(d.get('employment_type'), ''),
        'Original Min Compensation': lo, 'Original Max Compensation': hi,
        'Compensation Period': period, 'Compensation Currency': d.get('salary_currency'),
        'Compensation Evidence': d.get('salary_summary'),
        'Compensation Source': 'Upstream ATS fields; conversion uses 2080 h/year, 40 h/week, 8 h/day' if lo is not None or hi is not None else '',
        'Min Wage Normalized Hourly': normalize_hourly(lo, period),
        'Max Wage Normalized Hourly': normalize_hourly(hi, period),
        'Vaccine Requirement': 'Not Stated',
        'Date Scraped': datetime.now(timezone.utc).isoformat(timespec='seconds'),
    })
    # Returned jobs are advertised as active by the library; honor explicit expiry.
    row['Job Opportunity Stage'] = 'Open'
    if d.get('application_deadline'):
        try:
            deadline = datetime.fromisoformat(str(d['application_deadline']).replace('Z','+00:00'))
            if deadline.tzinfo is None: deadline = deadline.replace(tzinfo=timezone.utc)
            if deadline < datetime.now(timezone.utc): row['Job Opportunity Stage'] = 'Closed'
        except ValueError:
            pass
    return row


def scrape_upstream(url, max_jobs=None, debug_dir='debug', save_debug=True, progress_callback=None):
    if not available():
        raise RuntimeError('Optional ATS library missing. Install requirements-ats.txt with Python 3.11+.')
    cfg = board_config(url)
    with tempfile.TemporaryDirectory() as td:
        result = Path(td)/'result.json'
        request = dict(url=url, config=cfg, max_jobs=max_jobs, result=str(result))
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker'],
                              input=json.dumps(request), text=True, capture_output=True,
                              timeout=int(cfg.get('board_timeout_seconds', 180)))
        if proc.returncode:
            raise RuntimeError('ATS adapter failed: ' + (proc.stderr or proc.stdout)[-1500:])
        payload = json.loads(result.read_text())
    if save_debug:
        import hashlib
        folder=Path(debug_dir)/('ats_'+hashlib.sha256(url.encode()).hexdigest()[:12]);folder.mkdir(parents=True,exist_ok=True)
        (folder/'jobs.json').write_text(json.dumps(payload,indent=2))
    rows=[normalize_job(job,url) for job in payload]
    df=pd.DataFrame(rows).reindex(columns=CANONICAL_COLUMNS)
    if len(df):
        df=df[df['Job Opportunity Name'].fillna('').str.strip().ne('') & df['Job Detail URL'].fillna('').str.startswith(('https://','http://'))]
        df=df.drop_duplicates(subset=['Job Detail URL']).reset_index(drop=True)
    if progress_callback: progress_callback(len(df),len(df),'ATS extraction complete')
    return df


def worker():
    from ats_scrapers import get_scraper_for_url
    from ats_scrapers.scrapers import get_scraper
    req=json.load(sys.stdin);cfg=req['config'];kwargs=dict(cfg.get('adapter_options',{}))
    kwargs.setdefault('timeout',30.0);kwargs.setdefault('include_descriptions',True)
    scraper=(get_scraper(cfg['ats'],cfg['slug'],**kwargs) if cfg.get('ats') and cfg.get('slug')
             else get_scraper_for_url(req['url'],**kwargs))
    jobs=scraper.fetch()
    # Most upstream fetchers enumerate the full board; max_jobs limits returned
    # rows, not network work. The subprocess bounds total run time.
    if req['max_jobs'] is not None: jobs=jobs[:int(req['max_jobs'])]
    Path(req['result']).write_text(json.dumps([j.model_dump(mode='json') for j in jobs]))


if __name__=='__main__': worker()
