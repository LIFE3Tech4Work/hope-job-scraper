from scraper_router import detect_platform
from common_utils import extract_compensation, extract_hours
from oracle_scraper import choose_compensation, normalize_oracle_date

cases={
"https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=x&ccId=y":"ADP Workforce Now",
"https://myjobs.adp.com/brc/cx/job-listing":"ADP MyJobs",
"https://x.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs":"Oracle Recruiting Cloud",
"https://camba.org/careers/all-openings/":"CAMBA Careers",
"https://careers-sus.icims.com/jobs/search?ss=1":"iCIMS",
"https://www.paycomonline.net/v4/ats/web.php/portal/ABC/career-page":"Paycom",
"https://www.brooklynnavyyard.org/jobs-with-yard-businesses/":"Brooklyn Navy Yard aggregator",
}
for url,expected in cases.items():
    assert detect_platform(url)==expected,(url,detect_platform(url),expected)
    print(f"PASS: {expected} detected")
lo,hi,p,hlo,hhi,_=extract_compensation("Compensation: $27.00 hourly")
assert (lo,hi,p,hlo,hhi)==(27.0,27.0,"Hourly",27.0,27.0)
print("PASS: CAMBA single hourly compensation")
lo,hi,p,hlo,hhi,_=extract_compensation("Min USD $19.23/Hr. Max USD $19.23/Hr.")
assert lo==19.23 and hi==19.23 and p=="Hourly"
print("PASS: iCIMS min/max hourly compensation")
assert extract_hours("Status: Full-time/ Temporary (35 hours per week)")== (35.0,35.0)
print("PASS: weekly hours parsed")
comp=choose_compensation([("test","Salary Range: $165,000 - $215,000 annually")])
assert comp.minimum==165000 and comp.maximum==215000
print("PASS: Oracle salary range extracted")
assert normalize_oracle_date("2026-09-18T03:59:00.000+00:00")=="2026-09-17"
print("PASS: Oracle close date normalized")
print("All self-tests passed.")
