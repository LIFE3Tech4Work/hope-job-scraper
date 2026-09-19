import unittest
from unittest.mock import patch
import pandas as pd
from streamlit.testing.v1 import AppTest
from schema import CANONICAL_COLUMNS

class BatchUITest(unittest.TestCase):
    def test_failed_board_does_not_discard_other_results(self):
        df=pd.DataFrame([{'Job Opportunity Name':'Fixture job','Job Detail URL':'https://example.org/jobs/1'}]).reindex(columns=CANONICAL_COLUMNS)
        def scrape(url,**kwargs):
            if 'brc' in url: raise RuntimeError('Simulated unavailable board')
            return df.copy(),'Fixture adapter'
        with patch('scraper_router.scrape_jobs',side_effect=scrape):
            at=AppTest.from_file('app.py').run()
            at.text_area[0].set_value('https://myjobs.adp.com/brc/cx/job-listing\nhttps://camba.org/careers/all-openings/')
            at.button[0].click().run(timeout=20)
            self.assertEqual(len(at.exception),0)
            self.assertEqual(len(at.session_state['result_df']),1)
            statuses=at.session_state['board_results']['Status'].tolist()
            self.assertEqual(statuses[0],'Failed')
            self.assertIn('Returned jobs',statuses[1])
if __name__=='__main__':unittest.main()
