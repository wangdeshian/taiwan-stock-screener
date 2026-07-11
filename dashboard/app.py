from __future__ import annotations

import os

import pandas as pd
import requests
import streamlit as st


API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="Taiwan Stock AI Screener", layout="wide")
st.title("Taiwan Stock AI Screener V3")

if st.button("Run Daily Update"):
    response = requests.post(f"{API_BASE_URL}/jobs/update", timeout=30)
    st.write(response.json())

response = requests.get(f"{API_BASE_URL}/candidates", timeout=30)
response.raise_for_status()
candidates = response.json()

if not candidates:
    st.info("No candidates yet.")
else:
    df = pd.DataFrame(candidates)
    st.subheader("Top Candidates")
    st.dataframe(
        df[
            [
                "symbol",
                "name",
                "industry",
                "total_score",
                "entry_price",
                "stop_loss_price",
                "target_price_1",
                "target_price_2",
                "risk_reward_ratio",
            ]
        ],
        use_container_width=True,
    )

    selected = st.selectbox("Stock", df["symbol"].tolist())
    detail = df[df["symbol"] == selected].iloc[0].to_dict()
    st.json(detail)
