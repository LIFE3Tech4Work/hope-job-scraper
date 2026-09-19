import io
import json
import os
from ocr_fallback import OCRSettings
from pathlib import Path

import pandas as pd
import streamlit as st

from scraper_router import detect_platform, scrape_jobs
from ats_bridge import available

DEFAULT_URLS = """https://myjobs.adp.com/brc/cx/job-listing
https://camba.org/careers/all-openings/
https://www.brooklynnavyyard.org/jobs-with-yard-businesses/
https://www.paycomonline.net/v4/ats/web.php/portal/EADEA190247F3A600C15C7900B9DC6D1/career-page
https://careers-sus.icims.com/jobs/search?ss=1&mobile=false&width=1397&height=500&bga=true&needsRedirect=false&jan1offset=-300&jun1offset=-240"""
SUPPORTED = {"ADP Workforce Now","ADP MyJobs","Oracle Recruiting Cloud","CAMBA Careers","iCIMS","Paycom","Brooklyn Navy Yard aggregator"}

st.set_page_config(page_title="Local Job Scrubber", layout="wide")
st.title("Local Job Scrubber")
st.caption("Paste one or more public career/job-board URLs, one per line. The app auto-detects the platform, uses a dedicated adapter when available, and combines all jobs into one normalized output.")

raw_urls = st.text_area("Career site / job-board URLs (one per line)", value=DEFAULT_URLS, height=190)
urls = [x.strip() for x in raw_urls.splitlines() if x.strip()]
def safe_detect(url):
    try: return detect_platform(url)
    except Exception as exc: return str(exc)

if not available(): st.info("Broader ATS coverage is optional: install requirements-ats.txt with Python 3.11 or newer.")
summary = pd.DataFrame([{"URL":u,"Detected platform":safe_detect(u)} for u in urls])
if len(summary): st.dataframe(summary, use_container_width=True, hide_index=True)
unsupported=[u for u in urls if safe_detect(u) not in SUPPORTED and not safe_detect(u).startswith("ATS library / ")]
if unsupported: st.warning("Some URLs will use the generic browser fallback. Dedicated adapters are more reliable for compensation, pagination, IDs, and qualifications.")

col1,col2,col3=st.columns(3)
with col1: max_jobs=st.number_input("Jobs to test per board",min_value=1,max_value=1000,value=5,step=1)
with col2: save_debug=st.checkbox("Save raw debug files",value=True)
with col3: run_full=st.checkbox("Remove job result limit (coverage depends on adapter)",value=False)

with st.expander("Local screenshot OCR fallback"):
    ocr_enabled=st.checkbox("Enable local screenshot OCR with Tesseract",value=False)
    st.caption("Screenshots are processed on this computer. No API key, cloud model or per-call fees. Install Tesseract first. Evidence is saved locally and extracted fields require review.")
    ocr_postings=st.number_input("Maximum postings to OCR per board",min_value=1,max_value=100,value=5)
    st.caption("Each board receives its own OCR allowance. Five postings across eight boards allows up to 40 OCR attempts.")
    ocr_pages=st.number_input("Maximum screenshots per posting",min_value=1,max_value=12,value=6)
    ocr_language=st.text_input("Tesseract language",value="eng")
    screenshot_only=st.checkbox("Treat each URL as one posting and extract screenshots directly",value=False)

if st.button("Run scrape",type="primary"):
    if not urls: st.error("Enter at least one URL.")
    elif screenshot_only and not ocr_enabled: st.error("Enable screenshot extraction to use direct posting mode.")
    else:
        audits=[]
        all_frames=[]; board_results=[]; status=st.empty(); progress=st.progress(0)
        try:
            for board_index,url in enumerate(urls,1):
                ocr=OCRSettings(enabled=ocr_enabled,max_postings=int(ocr_postings),max_screenshots=int(ocr_pages),language=ocr_language)
                platform=safe_detect(url); status.write(f"Board {board_index}/{len(urls)}: {platform}")
                def update(i,total,title):
                    board_fraction=(board_index-1)/len(urls)
                    job_fraction=(i/max(total,1))/len(urls)
                    progress.progress(min(board_fraction+job_fraction,1.0))
                    status.write(f"Board {board_index}/{len(urls)} · {platform} · {i}/{total}: {title}")
                try:
                    df,used=scrape_jobs(url,max_jobs=None if run_full else int(max_jobs),debug_dir="debug",save_debug=save_debug,progress_callback=update,ocr=ocr,screenshot_only=screenshot_only)
                    notes="; ".join(df.attrs.get("warnings",[]))
                    audits.extend(df.attrs.get("ocr_audits",[]))
                    all_frames.append(df)
                    board_results.append({"Platform":used,"URL":url,"Jobs":len(df),"Status":"Review required" if notes else ("Returned jobs - coverage unverified" if len(df) else "No jobs returned - verify"),"Notes":notes})
                except Exception as exc:
                    board_results.append({"Platform":platform,"URL":url,"Jobs":0,"Status":"Failed","Notes":str(exc)})
                    st.warning(f"Could not scrape {url}: {exc}")
            df=pd.concat(all_frames,ignore_index=True) if all_frames else pd.DataFrame(columns=__import__("schema").CANONICAL_COLUMNS)
            Path("outputs").mkdir(exist_ok=True); df.to_csv("outputs/job_opportunities.csv",index=False); df.to_excel("outputs/job_opportunities.xlsx",index=False,engine="openpyxl")
            pd.DataFrame(board_results).to_csv("outputs/board_results.csv",index=False)
            Path("outputs/ocr_evidence.json").write_text(json.dumps(audits,indent=2))
            st.session_state["ocr_audits"]=audits
            st.session_state["result_df"]=df; st.session_state["board_results"]=pd.DataFrame(board_results)
            status.info(f"Batch finished: {len(df)} jobs; {sum(r['Status']=='Failed' for r in board_results)} failed board(s). See board results."); progress.progress(1.0)
        except Exception as exc: st.exception(exc)

if "result_df" in st.session_state:
    df=st.session_state["result_df"]; populated=df["Original Min Compensation"].notna().sum() if len(df) else 0
    a,b,c=st.columns(3); a.metric("Jobs scraped",len(df)); b.metric("Compensation populated",int(populated)); c.metric("Missing compensation",int(len(df)-populated))
    if "board_results" in st.session_state:
        st.subheader("Board results"); st.dataframe(st.session_state["board_results"],use_container_width=True,hide_index=True)
    if st.session_state.get("ocr_audits"):
        st.download_button("Download screenshot evidence index",data=json.dumps(st.session_state["ocr_audits"],indent=2),file_name="ocr_evidence.json",mime="application/json")
    st.subheader("Compensation check")
    st.dataframe(df[["Job Opportunity Name","Employer Name","Source","Original Min Compensation","Original Max Compensation","Compensation Period","Min Wage Normalized Hourly","Max Wage Normalized Hourly","Compensation Source","Compensation Evidence"]],use_container_width=True,hide_index=True)
    st.subheader("Full normalized results"); st.dataframe(df,use_container_width=True,hide_index=True)
    csv_bytes=df.to_csv(index=False).encode("utf-8"); excel_buffer=io.BytesIO()
    with pd.ExcelWriter(excel_buffer,engine="openpyxl") as writer: df.to_excel(writer,index=False,sheet_name="Jobs")
    excel_buffer.seek(0); d1,d2=st.columns(2)
    with d1: st.download_button("Download CSV",data=csv_bytes,file_name="job_opportunities.csv",mime="text/csv")
    with d2: st.download_button("Download Excel",data=excel_buffer.getvalue(),file_name="job_opportunities.xlsx",mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
