import unittest
from unittest.mock import patch
import pandas as pd
from ats_bridge import normalize_job
from generic_scraper import job_nodes
from scraper_router import detect_platform, scrape_jobs

class IntegrationTests(unittest.TestCase):
    def test_upstream_salary_preserves_unknown_period_and_currency(self):
        row=normalize_job({'title':'Tech','company':'X','url':'https://x.org/jobs/1','salary_min':80000,'salary_currency':'CAD'},'https://x.org')
        self.assertIsNone(row['Min Wage Normalized Hourly'])
        self.assertEqual(row['Compensation Currency'],'CAD')
        self.assertIsNone(row['Required Education'])
    def test_hourly_and_annual_salary(self):
        for unit,amt,expected in [('HOUR',25,25),('YEAR',52000,25)]:
            row=normalize_job({'title':'Tech','url':'https://x.org/jobs/1','salary_period':unit,'salary_min':amt},'https://x.org')
            self.assertEqual(row['Min Wage Normalized Hourly'],expected)
            self.assertIsNone(row['Original Max Compensation'])
    def test_fulltime_does_not_imply_permanent(self):
        row=normalize_job({'title':'Tech','url':'https://x.org/jobs/1','employment_type':'FULL_TIME'},'https://x.org')
        self.assertEqual(row['Job Term'],'')
    def test_expiry(self):
        row=normalize_job({'title':'Tech','url':'https://x.org/jobs/1','application_deadline':'2000-01-01T00:00:00Z'},'https://x.org')
        self.assertEqual(row['Job Opportunity Stage'],'Closed')
    def test_nested_jsonld(self):
        data={'@graph':[{'@type':'Organization'},{'@type':['Thing','JobPosting'],'title':'Tech'}]}
        self.assertEqual([x['title'] for x in job_nodes(data)],['Tech'])
    def test_bad_url(self):
        with self.assertRaises(ValueError):detect_platform('not a URL')
    def test_custom_override(self):
        with patch('scraper_router.board_config',return_value={'ats':'workday','slug':'https://x.wd5.myworkdayjobs.com/External'}):
            self.assertEqual(detect_platform('https://careers.x.org'),'ATS library / workday')
    def test_unlimited_reaches_generic(self):
        with patch('scraper_router.resolve',return_value=None),patch('scraper_router.scrape_generic',return_value=pd.DataFrame()) as fallback:
            scrape_jobs('https://example.org/careers',max_jobs=None)
            self.assertIsNone(fallback.call_args.kwargs['max_jobs'])
    def test_optional_dependency_absent(self):
        with patch('ats_bridge.available',return_value=False):
            from ats_bridge import resolve,scrape_upstream
            self.assertIsNone(resolve('https://jobs.lever.co/example'))
            with self.assertRaisesRegex(RuntimeError,'requirements-ats'):scrape_upstream('https://jobs.lever.co/example')

if __name__=='__main__':unittest.main()
