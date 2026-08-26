"""
Streamlit UI for the Zycus AI Engineer Assessment.

Clean, simple, professional interface for:
  - Tab 1: Ticket Triage   (calls POST /triage)
  - Tab 2: Account Health  (calls POST /account-health)

Run with:
    python -m streamlit run streamlit_app.py
"""
import os
import time
import requests
import streamlit as st
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration & Constants
# ---------------------------------------------------------------------------

load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")

_EXAMPLE_TICKET = {
    "subject": "Unable to connect DataBridge Pro to Salesforce Connector",
    "body": (
        "Our production DataBridge Pro pipeline has been failing to connect to "
        "the Salesforce Connector since yesterday morning. We are seeing an "
        "ERR_CONNECTION_TIMEOUT error after 30 seconds on every attempt. "
        "Approximately 45 users are affected and critical ETL jobs are not completing. "
        "We are on version 3.1.2 and have not made any recent configuration changes."
    ),
}

_EXAMPLE_ACCOUNTS = ["ACC-3336", "ACC-3033", "ACC-7893", "ACC-4654", "ACC-2944"]

# ---------------------------------------------------------------------------
# Page Configuration
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Customer Support & Account Health Assistant",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Session State Initialization & Callbacks
# ---------------------------------------------------------------------------

if "txt_subject" not in st.session_state:
    st.session_state["txt_subject"] = ""
if "txt_body" not in st.session_state:
    st.session_state["txt_body"] = ""
if "txt_account_id" not in st.session_state:
    st.session_state["txt_account_id"] = ""
if "triage_result" not in st.session_state:
    st.session_state["triage_result"] = None
if "health_result" not in st.session_state:
    st.session_state["health_result"] = None


def _load_example_ticket():
    """Callback to instantly populate the ticket subject and body."""
    st.session_state["txt_subject"] = _EXAMPLE_TICKET["subject"]
    st.session_state["txt_body"] = _EXAMPLE_TICKET["body"]


def _set_example_account(acc_id: str):
    """Callback to instantly populate the account ID input."""
    st.session_state["txt_account_id"] = acc_id


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("Customer Support & Account Health Assistant")
st.caption("Quickly triage tickets and prepare QBR briefs.")
st.markdown("---")

# ---------------------------------------------------------------------------
# Tabs Navigation
# ---------------------------------------------------------------------------

tab_triage, tab_health = st.tabs(["Ticket Triage", "Account Health"])


# ===========================================================================
# TAB 1 — TICKET TRIAGE
# ===========================================================================

with tab_triage:
    st.subheader("Ticket Triage")
    st.write(
        "Paste a customer's support request below. The assistant will identify "
        "the issue, urgency, relevant knowledge-base guidance, and suggested response."
    )

    # ── Example Loader ───────────────────────────────────────────────────
    col_ex1, _ = st.columns([1, 4])
    with col_ex1:
        st.button(
            "📋 Load Example Ticket",
            on_click=_load_example_ticket,
            help="Populate inputs with a sample DataBridge Pro issue",
        )

    # ── Inputs ───────────────────────────────────────────────────────────
    subject = st.text_input(
        "Subject",
        key="txt_subject",
        placeholder="Unable to connect DataBridge Pro to Connectors...",
    )

    body = st.text_area(
        "Ticket description",
        key="txt_body",
        placeholder="Paste the customer's message here...",
        height=160,
    )

    col_btn, _ = st.columns([1, 3])
    with col_btn:
        analyze_clicked = st.button("Analyze Ticket", type="primary", use_container_width=True)

    if analyze_clicked:
        if not subject.strip() or not body.strip():
            st.warning("Please provide both a subject and ticket description.")
        else:
            with st.spinner("Analyzing ticket with AI assistant..."):
                t0 = time.time()
                try:
                    resp = requests.post(
                        f"{BACKEND_URL}/triage",
                        json={"subject": subject.strip(), "body": body.strip()},
                        timeout=90,
                    )

                    if resp.status_code == 200:
                        triage_data = resp.json()
                        st.session_state["triage_result"] = triage_data
                        st.session_state["display_first_response"] = triage_data.get("first_response", "")
                    elif resp.status_code == 422:
                        st.error("Invalid ticket format. Please check the subject and body.")
                    elif resp.status_code == 502:
                        st.error("The AI assistant encountered an upstream model error. Please try again.")
                    else:
                        st.error(f"Server returned status {resp.status_code}. Please try again.")

                except requests.exceptions.ConnectionError:
                    st.error(
                        "Cannot connect to the backend server at " + BACKEND_URL + ". "
                        "Please make sure your FastAPI server is running: `uvicorn src.main:app --reload`."
                    )
                except requests.exceptions.Timeout:
                    st.error("The request timed out. The upstream model is experiencing high demand. Please try again.")
                except Exception as ex:
                    st.error("An unexpected error occurred. Please try again.")

    # ── Display Triage Result if available ───────────────────────────────
    if st.session_state["triage_result"]:
        data = st.session_state["triage_result"]

        st.markdown("---")
        st.subheader("Triage Summary")

        # Metrics Card
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric(label="Product Area", value=data.get("product_area") or "General / Billing")
            c2.metric(label="Issue Category", value=data.get("issue_category") or "—")
            
            urg = data.get("urgency", "—")
            urg_map = {"P1": "🔴 P1", "P2": "🟠 P2", "P3": "🔵 P3", "P4": "🟢 P4"}
            c3.metric(label="Urgency", value=urg_map.get(urg, urg))
            
            c4.metric(label="Recommended Team", value=data.get("recommended_responder_team") or "—")

        # Classification Reasoning
        st.subheader("Why this was classified this way")
        with st.container(border=True):
            st.write(data.get("reasoning", "No reasoning provided."))

        # Knowledge Base Reference
        st.subheader("Knowledge Base")
        with st.container(border=True):
            is_known = data.get("known_issue", False)
            kb_doc = data.get("knowledge_base_document")
            kb_ref = data.get("knowledge_base_reference")

            if is_known and kb_doc:
                st.success("✓ Known issue documented in knowledge base")
                st.markdown(f"**Document:** `{kb_doc}`")
                if kb_ref:
                    st.markdown(f"**Section:** {kb_ref}")
            else:
                st.info("No matching knowledge-base guidance was found.")

        # Suggested First Response
        st.subheader("Suggested First Response")
        first_resp = data.get("first_response", "")
        with st.container(border=True):
            st.write(first_resp)
        
        st.text_area(
            label="Editable message (ready to customize or copy):",
            value=first_resp,
            height=120,
            key=f"edit_resp_{hash(first_resp)}"
        )


# ===========================================================================
# TAB 2 — ACCOUNT HEALTH
# ===========================================================================

with tab_health:
    st.subheader("Account Health")
    st.write(
        "Enter a customer account ID to generate a concise health brief using "
        "the account profile and recent support history."
    )

    # ── Example Account Buttons ──────────────────────────────────────────
    st.write("**Quick Select Example Account:**")
    acc_cols = st.columns(len(_EXAMPLE_ACCOUNTS))
    for i, ex_acc in enumerate(_EXAMPLE_ACCOUNTS):
        acc_cols[i].button(
            ex_acc,
            key=f"btn_acc_{ex_acc}",
            on_click=_set_example_account,
            args=(ex_acc,),
            use_container_width=True,
        )

    account_id = st.text_input(
        "Account ID",
        key="txt_account_id",
        placeholder="Example: ACC-3336",
    )

    col_btn_h, _ = st.columns([1, 3])
    with col_btn_h:
        generate_clicked = st.button("Generate Account Brief", type="primary", use_container_width=True)

    if generate_clicked:
        if not account_id.strip():
            st.warning("Please enter an Account ID.")
        else:
            with st.spinner(f"Generating account brief for {account_id.strip()}..."):
                try:
                    resp = requests.post(
                        f"{BACKEND_URL}/account-health",
                        json={"account_id": account_id.strip()},
                        timeout=90,
                    )

                    if resp.status_code == 200:
                        st.session_state["health_result"] = resp.json()
                    elif resp.status_code == 404:
                        st.session_state["health_result"] = None
                        st.error("Account not found. Please check the account ID and try again.")
                    elif resp.status_code == 422:
                        st.session_state["health_result"] = None
                        st.error("Invalid account ID format.")
                    elif resp.status_code == 502:
                        st.session_state["health_result"] = None
                        st.error("The AI assistant encountered an upstream model error. Please try again.")
                    else:
                        st.session_state["health_result"] = None
                        st.error(f"Server returned status {resp.status_code}. Please try again.")

                except requests.exceptions.ConnectionError:
                    st.error(
                        "Cannot connect to the backend server at " + BACKEND_URL + ". "
                        "Please make sure your FastAPI server is running: `uvicorn src.main:app --reload`."
                    )
                except requests.exceptions.Timeout:
                    st.error("The request timed out. Please try again in a few moments.")
                except Exception as ex:
                    st.error("An unexpected error occurred. Please try again.")

    # ── Display Account Health Result if available ───────────────────────
    if st.session_state["health_result"]:
        h_data = st.session_state["health_result"]
        company = h_data.get("company", account_id.strip())

        st.markdown("---")
        st.subheader(f"Account Brief: {company}")

        # Executive Summary
        st.markdown("#### Executive Summary")
        with st.container(border=True):
            st.write(h_data.get("executive_summary", "No summary available."))

        # Open Risks & Flagged Issues
        st.markdown("#### Open Risks & Flagged Issues")
        risks = h_data.get("open_risks") or []

        if risks:
            for idx, r in enumerate(risks, start=1):
                severity = r.get("severity", "Low")
                sev_icons = {
                    "High": "🔴 High Severity",
                    "Medium": "🟠 Medium Severity",
                    "Low": "🟡 Low Severity",
                }
                sev_label = sev_icons.get(severity, f"Severity: {severity}")

                with st.container(border=True):
                    st.markdown(f"**{idx}. {r.get('risk', 'Identified Risk')}** — `{sev_label}`")
                    st.write(f"**Reason:** {r.get('reason', '')}")

                    evidence = r.get("evidence") or {}
                    tkt_id = evidence.get("ticket_id")
                    quote = evidence.get("quote")

                    if tkt_id or quote:
                        with st.expander("Supporting Evidence from Ticket History"):
                            if tkt_id:
                                st.write(f"**Ticket Reference:** `{tkt_id}`")
                            if quote:
                                st.info(f'"{quote}"')
        else:
            st.success("No significant risks were identified from the available data.")

        # Recommended Talking Points
        st.markdown("#### Recommended Talking Points")
        talking_points = h_data.get("talking_points") or []

        if talking_points:
            with st.container(border=True):
                for idx, tp in enumerate(talking_points, start=1):
                    st.write(f"**{idx}.** {tp}")
        else:
            st.info("No talking points were generated for this account.")


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.markdown("---")
st.caption("Zycus AI Engineer Assessment · Customer Support & Account Health Assistant")
