import streamlit as st
from groq import Groq

st.title("DarwinBox Data QA")

if st.button("Test Groq"):
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": "Reply with exactly: Groq connection successful"}],
    )
    st.success(response.choices[0].message.content)
