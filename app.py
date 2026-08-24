import streamlit as st
import sqlite3
import pandas as pd
import requests

KEY = st.secrets["ANTHROPIC_API_KEY"]

conn = sqlite3.connect("kajlia_demo.db")
payments = pd.read_sql("SELECT * FROM payments", conn)
flats = pd.read_sql("SELECT * FROM flats", conn)


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
 
def get_schema():
    lines = []
    for t in ["payments", "flats"]:
        cols = pd.read_sql(f"PRAGMA table_info({t})", conn)
        col_list = ", ".join(f"{r['name']} ({r['type']})" for _, r in cols.iterrows())
        lines.append(f"Table {t}: {col_list}")

    lines.append("Note: payments.date is stored as TEXT in YYYY-MM-DD format.")
    lines.append("Note: cheque_status has values: n/a, pending, realized_bank, realized_cash, returned. A payment counts as cleared if cheque_status is NOT 'pending' and NOT 'returned'.")
    lines.append("Note: ignore the is_returned column, it is unused and always 0.")

    return "\n".join(lines)
question = st.text_input("Apna sawal likhein")

if question:
    sql_prompt = f"""You are a SQLite expert. Write ONE SQL query to answer the question.

{get_schema()}

Rules:
- Return ONLY the SQL query. No explanation, no markdown, no backticks.
- Use SELECT only. Never use INSERT, UPDATE, DELETE, DROP, or ALTER.
- Amounts are in rupees, not crore.

Question: {question}"""

    sql = llm_call(sql_prompt).strip()
    with st.expander("SQL dekhein"):
        st.code(sql, language="sql")
    forbidden = ["insert", "update", "delete", "drop", "alter", "create", "replace"]
    lower = sql.lower()

    if not lower.startswith("select"):
        st.error("Sirf SELECT queries chal sakti hain.")
    elif any(word in lower for word in forbidden):
        st.error("Ye query mehfooz nahi hai.")
    else:
        try:
            result = pd.read_sql(sql, conn)
        except Exception as e:
            st.error("Ye query chal nahi saki. Sawal thora alag tareeqe se likh kar dekhein.")
            with st.expander("Technical details"):
                st.write(str(e))
            st.stop()

        with st.expander("Raw result"):
            st.write(result)

        result_cr = result.copy()
        for c in result_cr.columns:
            if result_cr[c].dtype.kind in "if":
                result_cr[c] = (result_cr[c] / 10_000_000).round(2)

        answer_prompt = f"""Answer the question in one or two sentences based only on this result.

Question: {question}
SQL result (all amounts already in crore): {result_cr.to_string()}

Rules:
- Numbers are already in crore. Use them exactly as given.
- Do NOT calculate anything yourself.
- No recommendations, no advice.
- If the result is empty, say no data was found."""

        st.write(llm_call(answer_prompt))