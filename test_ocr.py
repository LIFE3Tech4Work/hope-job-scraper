import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from PIL import Image,ImageDraw,ImageFont
from ocr_fallback import OCRSettings,read_image,parse_fields,extract_page
from schema import CANONICAL_COLUMNS
from scraper_router import scrape_jobs
SAMPLE=['Job Title: HVAC Technician','Company: Example Energy','Location: New York, NY','Job Description:','Responsibilities: Maintain heating systems and inspect equipment.','Pay: USD $25 - $30 per hour','Schedule: 35 hours per week','High school diploma required.','EPA 608 certification required.','OSHA 30 preferred.']
def lines(text=SAMPLE):return [{'text':s,'confidence':95,'accepted':True,'image':'fixture.png'} for s in text]
class OCRTests(unittest.TestCase):
    def test_local_engine_reads_generated_image(self):
        with tempfile.TemporaryDirectory() as td:
            im=Image.new('RGB',(1500,900),'white');d=ImageDraw.Draw(im)
            fonts=['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','/System/Library/Fonts/Helvetica.ttc']
            font=ImageFont.truetype(next((f for f in fonts if Path(f).exists()),'DejaVuSans.ttf'),30)
            for i,s in enumerate(SAMPLE):d.text((40,30+i*70),s,font=font,fill='black')
            p=Path(td)/'posting.png';im.save(p)
            recognized,tsv=read_image(p,OCRSettings(enabled=True))
            row,evidence=parse_fields(recognized,'https://example.org/jobs/1','https://example.org')
            self.assertEqual(row['Job Opportunity Name'],'HVAC Technician')
            self.assertEqual(row['Original Min Compensation'],25);self.assertEqual(row['Original Max Compensation'],30)
            self.assertEqual(row['Min Hours per Week'],35);self.assertEqual(row['Compensation Currency'],'USD')
            self.assertIn('EPA 608',row['Required Certifications & Licenses'])
    def test_preferred_not_required_and_no_guessed_currency(self):
        row,_=parse_fields(lines(['Job Title: Technician','Job Description: Maintain equipment','OSHA 30 preferred.','Pay: $25 per hour','Employment type: Full-time']),'u','u')
        self.assertIsNone(row['Required Certifications & Licenses']);self.assertIsNone(row['Compensation Currency']);self.assertIsNone(row['Job Term'])
    def test_no_guessed_annual_salary(self):
        row,_=parse_fields(lines(['Job Title: Technician','Job Description: Maintenance duties','Salary: $50,000']),'u','u');self.assertIsNone(row['Original Min Compensation'])
    def test_blocked_and_listing_pages(self):
        for extra in ['Verify you are human','10 jobs found']:
            with self.assertRaises(ValueError):parse_fields(lines(SAMPLE+[extra]),'u','u')
    def test_low_confidence_lines_not_used(self):
        data=lines();data[5]['accepted']=False
        row,_=parse_fields(data,'u','u');self.assertIsNone(row['Original Min Compensation'])
    def test_fill_only_missing_fields(self):
        base=pd.DataFrame([{'Job Opportunity Name':'Technician','Job Description':'Existing description','Employer Name':'Verified Employer','Job Detail URL':'https://example.org/jobs/1','Original Min Compensation':40}]).reindex(columns=CANONICAL_COLUMNS)
        extra=pd.DataFrame([{'Employer Name':'OCR employer','Original Min Compensation':25,'Original Max Compensation':30,'Location':'NYC'}]).reindex(columns=CANONICAL_COLUMNS);extra.attrs['ocr_audits']=[{'audit_file':'fixture.json'}]
        with patch('scraper_router._scrape_jobs',return_value=(base,'test')),patch('ocr_fallback.scrape_posting_screenshot',return_value=extra):
            df,_=scrape_jobs('https://example.org/jobs',ocr=OCRSettings(enabled=True))
        self.assertEqual(df.iloc[0]['Employer Name'],'Verified Employer');self.assertEqual(df.iloc[0]['Original Min Compensation'],40);self.assertEqual(df.iloc[0]['Location'],'NYC');self.assertTrue(pd.isna(df.iloc[0]['Original Max Compensation']))
    def test_budget_stops_before_capture(self):
        with self.assertRaisesRegex(RuntimeError,'limit'):extract_page(None,'u','u',OCRSettings(enabled=True,max_postings=1,postings_used=1))
    def test_primary_failure_routes_to_browser_ocr(self):
        df=pd.DataFrame([{'Job Opportunity Name':'Tech'}]).reindex(columns=CANONICAL_COLUMNS)
        with patch('scraper_router.detect_platform',return_value='ADP MyJobs'),patch('scraper_router._scrape_jobs',side_effect=RuntimeError('fixture failure')),patch('scraper_router.scrape_generic',return_value=df) as fallback:
            result,used=scrape_jobs('https://example.org/jobs',ocr=OCRSettings(enabled=True));self.assertTrue(fallback.call_args.kwargs['ocr'].enabled);self.assertIn('fallback',used)
    def test_disabled_ocr_does_not_recover(self):
        with patch('scraper_router._scrape_jobs',side_effect=RuntimeError('fixture failure')),patch('scraper_router.scrape_generic') as fallback:
            with self.assertRaises(RuntimeError):scrape_jobs('https://example.org/jobs')
            fallback.assert_not_called()
if __name__=='__main__':unittest.main()
