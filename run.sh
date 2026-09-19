#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
fi
exec streamlit run app.py --server.address 127.0.0.1 --server.port 8501
