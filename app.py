import streamlit as st
import sqlite3
import pandas as pd
import requests

KEY = st.secrets["ANTHROPIC_API_KEY"]

conn = sqlite3.connect("kajlia_demo.db")
payments = pd.read_sql("SELECT * FROM payments", conn)
flats = pd.read_sql("SELECT * FROM flats", conn)

st.write("payments:", payments.shape)
st.write(payments.head())
st.write("flats:", flats.shape)
st.write(flats.head())
payments["date"] = pd.to_datetime(payments["date"], errors="coerce")

cleared = payments[~payments["cheque_status"].isin(["pending", "returned"])]
total_recovery = cleared["amount"].sum()

flat_sale = flats["sale"].sum()
outstanding = flat_sale - total_recovery

pending = payments.loc[payments["cheque_status"] == "pending", "amount"].sum()
unsecured = outstanding - pending
total_recovery_cr = total_recovery / 10_000_000
outstanding_cr = outstanding / 10_000_000
unsecured_cr = unsecured / 10_000_000
pending_cr = pending / 10_000_000
st.title("Kajlia Recovery")

col1, col2, col3 = st.columns(3)
col1.metric("Recovered", f"{total_recovery_cr:.2f} cr")
col2.metric("Outstanding", f"{outstanding_cr:.1f} cr")
col3.metric("Unsecured", f"{unsecured_cr:.1f} cr")
def llm_call(prompt):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        },
        json={
            "model": "claude-sonnet-4-5",
            "max_tokens": 800,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}]
        }
    )
    return r.json()["content"][0]["text"]
if st.button("Test"):
    import json
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        },
        json={
            "model": "claude-sonnet-4-5",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Say hello in 5 words"}]
        }
    )
    st.code(json.dumps(r.json(), indent=2))
    st.write("TEXT wala:")
    st.write(type(r.text))      # <class 'str'>

    st.write("JSON wala:")
    st.write(type(r.json()))    # <class 'dict'>
if st.button("Explain the numbers"):
    prompt = f"""Numbers below are already in crore. Use them exactly as given.
Do NOT recalculate. Do not add, subtract, or compute anything yourself.
No recommendations. No advice. State only what the data shows.
If something is missing, say what is missing.

<data>
Total recovery: {total_recovery_cr:.2f} crore
Outstanding: {outstanding_cr:.1f} crore
Pending cheques: {pending_cr:.2f} crore
Unsecured: {unsecured_cr:.1f} crore
</data>

Summarise the position in three sentences."""

    st.write(llm_call(prompt))
    