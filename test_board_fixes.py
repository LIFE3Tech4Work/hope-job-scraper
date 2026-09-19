import unittest
from unittest.mock import Mock, patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from icims_scraper import _load_content, scrape_icims
from scraper_router import detect_platform
from schema import CANONICAL_COLUMNS


class BoardFixTests(unittest.TestCase):
    def test_adp_root_and_listing_use_same_adapter(self):
        for suffix in ('cx', 'cx/', 'cx/job-listing', 'cx?lang=en'):
            self.assertEqual(detect_platform('https://myjobs.adp.com/brc/' + suffix), 'ADP MyJobs')

    def test_icims_frames_pagination_and_detail_fields(self):
        base = 'https://careers-sus.icims.com'
        pages = {
            '/jobs/search': '<iframe src="https://example.org/tracker"></iframe><iframe src="/jobs/search?in_iframe=1"></iframe>',
            '/jobs/search?in_iframe=1': '<a href="/jobs/1/porter/job">Porter</a><a aria-label="Next page of results" href="/jobs/search?page=2">Next</a>',
            '/jobs/search?page=2': '<a href="/jobs/1/porter/job">Porter</a><a href="/jobs/2/technician/job">Technician</a>',
            '/jobs/1/porter/job': '<iframe src="/jobs/1/porter/job?in_iframe=1"></iframe>',
            '/jobs/1/porter/job?in_iframe=1': '<h1>Porter</h1><div>Min</div><div>USD $20/Hr.</div><div>Max</div><div>USD $25/Hr.</div><h2>Qualifications</h2><p>High school diploma required.</p>',
            '/jobs/2/technician/job': '<h1>Technician</h1><p>Maintenance duties</p>',
        }
        def get(url, **kwargs):
            return Mock(url=url, text=pages[url.removeprefix(base)], raise_for_status=Mock())
        session = Mock()
        session.get.side_effect = get
        with patch('icims_scraper.requests.Session', return_value=session):
            df = scrape_icims(base + '/jobs/search', save_debug=False)
        self.assertEqual(df['Job Opportunity Name'].tolist(), ['Porter', 'Technician'])
        self.assertEqual(df.iloc[0]['Original Min Compensation'], 20)
        self.assertEqual(df.iloc[0]['Original Max Compensation'], 25)
        self.assertEqual(df.iloc[0]['Compensation Period'], 'Hourly')

    def test_frame_loop_stops(self):
        url = 'https://careers-sus.icims.com/jobs/search'
        session = Mock()
        session.get.return_value = Mock(url=url, text='<iframe src="/jobs/search"></iframe>')
        with self.assertRaisesRegex(RuntimeError, 'loop'):
            _load_content(session, url)

    def test_ocr_budget_is_fresh_after_board_failure(self):
        budgets = []
        def scrape(url, **kwargs):
            settings = kwargs['ocr']
            budgets.append(settings)
            self.assertEqual(settings.postings_used, 0)
            settings.postings_used = settings.max_postings
            if len(budgets) == 1:
                raise RuntimeError('First board exhausted its attempts')
            return pd.DataFrame([{'Job Opportunity Name': 'Second board job'}]).reindex(columns=CANONICAL_COLUMNS), 'Fixture'
        with patch('scraper_router.scrape_jobs', side_effect=scrape):
            app = AppTest.from_file('app.py').run()
            app.text_area[0].set_value('https://myjobs.adp.com/brc/cx\nhttps://careers-sus.icims.com/jobs/search')
            next(c for c in app.checkbox if c.label == 'Enable local screenshot OCR with Tesseract').check()
            app.button[0].click().run(timeout=20)
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.session_state['result_df']), 1)
        self.assertEqual(len(budgets), 2)
        self.assertIsNot(budgets[0], budgets[1])


if __name__ == '__main__':
    unittest.main()
