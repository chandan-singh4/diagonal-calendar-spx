"""
app.py — Dashboard.  Run with: streamlit run app.py

Layout (top to bottom):
  Header — SPX price, auto-refresh status
  Two-column:
    LEFT  — Symbol, expiry selectors + DTE/change metrics, strike selector
    RIGHT — IV metric strip, time-range selector,
             [TOP CHART]    selected-strike IV  (front vs back + ratio)
             [BOTTOM CHART] ATM IV              (front vs back + ratio)
             Historical range stats
  Trade Quality Score
  Options chain table
"""
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import config
import db
import demo_data
import iv_engine
import schwab_client

st.set_page_config(page_title="SPX Diagonal Calendar Analyzer", layout="wide")

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("Settings")
demo_mode = st.sidebar.toggle(
    "Demo Mode (synthetic data, no API needed)", value=config.DEMO_MODE
)
if demo_mode:
    st.sidebar.caption(
        "Showing synthetic data. Toggle off once your Schwab credentials are in .env."
    )

st_autorefresh(interval=config.POLL_INTERVAL_SECONDS * 1000, key="autorefresh")


@st.cache_resource
def get_client():
    return schwab_client.get_client()


# ---------------------------------------------------------------------------
# Pull data
# ---------------------------------------------------------------------------
if demo_mode:
    demo_data.seed_if_empty()
    db_path = config.DEMO_DB_PATH
    spx_price = demo_data.get_demo_quote()
    chain_df = demo_data.generate_synthetic_chain(spx_price)
    available_expiries = [demo_data.DEMO_FRONT, demo_data.DEMO_BACK]
    front_expiry, front_dte = demo_data.DEMO_FRONT, demo_data.DEMO_FRONT_DTE
    back_expiry, back_dte = demo_data.DEMO_BACK, demo_data.DEMO_BACK_DTE
else:
    try:
        client = get_client()
        spx_price = schwab_client.get_spx_quote(client)
        raw_chain = schwab_client.get_option_chain(
            client,
            from_date=date.today(),
            to_date=date.today() + timedelta(days=45),
        )
        chain_df = schwab_client.chain_to_dataframe(raw_chain)
    except Exception as e:
        st.error(f"Couldn't reach Schwab API: {e}")
        st.stop()

    db_path = config.DB_PATH
    available_expiries = sorted(chain_df["expiry"].unique())
    if len(available_expiries) < 2:
        st.warning(
            "Need at least two expirations in range — "
            "try widening the date range in get_option_chain()."
        )
        st.stop()

db.init_db(db_path)

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("SPX Diagonal Calendar Analyzer")
st.caption(
    f"SPX: **{spx_price:,.2f}**  |  Auto-refresh every {config.POLL_INTERVAL_SECONDS}s"
    + ("  |  ⚠️ DEMO DATA" if demo_mode else "  |  🟢 Live")
)

# ---------------------------------------------------------------------------
# Two-column layout
# ---------------------------------------------------------------------------
left, right = st.columns([1, 3])

# ── LEFT PANEL ──────────────────────────────────────────────────────────────
with left:
    st.subheader("Expirations")
    if not demo_mode:
        default_back = min(1, len(available_expiries) - 1)
        front_expiry = st.selectbox(
            "Front", available_expiries, index=0, key="front_expiry_select"
        )
        back_expiry = st.selectbox(
            "Back", available_expiries, index=default_back, key="back_expiry_select"
        )
        if back_expiry <= front_expiry:
            st.warning("Back expiry ≤ Front — unusual for a diagonal, shown anyway.")
        front_dte = int(chain_df[chain_df["expiry"] == front_expiry]["dte"].iloc[0])
        back_dte = int(chain_df[chain_df["expiry"] == back_expiry]["dte"].iloc[0])
    else:
        st.selectbox("Front", [front_expiry], index=0, disabled=True)
        st.selectbox("Back", [back_expiry], index=0, disabled=True)

    # Day-change metrics per expiry
    for label, exp, dte in [
        ("Front", front_expiry, front_dte),
        ("Back", back_expiry, back_dte),
    ]:
        rows = db.get_latest_two_snapshots(exp, db_path)
        if rows:
            latest = rows[0]["atm_iv"]
            change = (latest - rows[1]["atm_iv"]) if len(rows) == 2 else 0.0
            st.metric(f"{label} ({dte} DTE)", f"{latest:.2f}%", f"{change:+.2f}")

    st.divider()

    # ── Strike selector ──────────────────────────────────────────────────────
    st.subheader("Strike Selection")
    st.caption(
        "Same strikes applied to both front (short) and back (long) expiries."
    )

    # Default call strike: nearest 5-multiple at-or-above spot
    # Default put strike: 100 points below that — a starting suggestion, not a recommendation
    default_call = float(round(spx_price / 5) * 5)
    default_put = float(round((spx_price - 100) / 5) * 5)

    call_strike = st.number_input(
        "Call Strike (OTM / short call)",
        min_value=1000.0, max_value=15000.0,
        value=default_call, step=5.0, format="%.0f",
        key="call_strike_input",
        help="Strike for: Sell front CALL / Buy back CALL",
    )
    put_strike = st.number_input(
        "Put Strike (OTM / short put)",
        min_value=1000.0, max_value=15000.0,
        value=default_put, step=5.0, format="%.0f",
        key="put_strike_input",
        help="Strike for: Sell front PUT / Buy back PUT",
    )

    strikes_set = call_strike > 0 and put_strike > 0

    # Live contract lookup — shows what you'd actually be trading right now
    if strikes_set:
        st.caption("**Current contract data:**")
        for side, strike, label in [
            ("CALL", call_strike, "Call"),
            ("PUT", put_strike, "Put"),
        ]:
            fc = iv_engine.strike_contract(chain_df, front_expiry, strike, side)
            bc = iv_engine.strike_contract(chain_df, back_expiry, strike, side)
            if not fc.found_exact:
                st.warning(f"{label} strike {strike:.0f} not found — showing nearest {fc.strike:.0f}")
            f_iv_str = f"{fc.iv:.2f}%" if fc.iv else "N/A"
            b_iv_str = f"{bc.iv:.2f}%" if bc.iv else "N/A"
            ratio_str = (
                f"{fc.iv / bc.iv:.4f}" if fc.iv and bc.iv else "N/A"
            )
            st.markdown(
                f"**{label} {strike:.0f}** — Front IV: {f_iv_str} | "
                f"Back IV: {b_iv_str} | Ratio: {ratio_str}"
            )


# ── RIGHT PANEL ──────────────────────────────────────────────────────────────
with right:

    # ── ATM IV term structure (top metric strip) ─────────────────────────────
    front_iv = iv_engine.atm_iv(chain_df, front_expiry, spx_price)
    back_iv = iv_engine.atm_iv(chain_df, back_expiry, spx_price)
    ts = iv_engine.term_structure(front_iv, back_iv)
    iv_index = float(chain_df.groupby("expiry")["iv"].mean().mean())

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("IV Ratio (F/B) — ATM", f"{ts.ratio:.4f}")
    m2.metric("Front IV % — ATM", f"{ts.front_iv:.2f}%")
    m3.metric("Back IV % — ATM", f"{ts.back_iv:.2f}%")
    m4.metric("IV Index (all expiries)", f"{iv_index:.2f}%")
    st.info(iv_engine.interpret_curve(ts))

    # ── Save snapshots ───────────────────────────────────────────────────────
    snap_ts = datetime.now(timezone.utc).isoformat()

    # ATM snapshot for every visible expiry
    for exp in chain_df["expiry"].unique():
        try:
            exp_dte = int(chain_df[chain_df["expiry"] == exp]["dte"].iloc[0])
            exp_iv = iv_engine.atm_iv(chain_df, exp, spx_price)
            db.save_expiry_snapshot(
                spx_price, exp, exp_dte, exp_iv, db_path=db_path, timestamp=snap_ts
            )
        except (ValueError, IndexError):
            continue

    # Strike-specific snapshot — only when strikes are entered
    if strikes_set:
        for side, strike in [("CALL", call_strike), ("PUT", put_strike)]:
            for exp in [front_expiry, back_expiry]:
                c = iv_engine.strike_contract(chain_df, exp, strike, side)
                db.save_strike_snapshot(
                    underlying_price=spx_price,
                    expiry=exp,
                    strike=c.strike,   # use the actual (possibly-snapped) strike
                    side=side,
                    iv=c.iv,
                    bid=c.bid,
                    ask=c.ask,
                    volume=c.volume,
                    open_interest=c.open_interest,
                    db_path=db_path,
                    timestamp=snap_ts,
                )

    # ── Time range selector ──────────────────────────────────────────────────
    period_label = st.radio(
        "Range", ["Today", "5D", "10D", "15D", "1M"],
        horizontal=True, label_visibility="collapsed"
    )
    period_days = {"Today": 1, "5D": 5, "10D": 10, "15D": 15, "1M": 30}[period_label]
    since_iso = (datetime.now(timezone.utc) - timedelta(days=period_days)).isoformat()

    # ── Fetch ATM history ────────────────────────────────────────────────────
    front_hist = pd.DataFrame(
        [dict(r) for r in db.get_expiry_history(front_expiry, since_iso, db_path)]
    )
    back_hist = pd.DataFrame(
        [dict(r) for r in db.get_expiry_history(back_expiry, since_iso, db_path)]
    )
    atm_merged = pd.DataFrame()
    if not front_hist.empty and not back_hist.empty:
        atm_merged = pd.merge(
            front_hist[["timestamp", "atm_iv"]].rename(columns={"atm_iv": "front_iv"}),
            back_hist[["timestamp", "atm_iv"]].rename(columns={"atm_iv": "back_iv"}),
            on="timestamp", how="inner",
        )
        atm_merged["iv_ratio"] = atm_merged["front_iv"] / atm_merged["back_iv"]
        atm_merged["timestamp"] = pd.to_datetime(atm_merged["timestamp"], format="ISO8601")

    # ── TOP CHART — Selected-strike IV ──────────────────────────────────────
    if strikes_set:
        st.markdown("#### Selected-Strike IV  *(your actual trade contracts)*")

        # Fetch strike history for the selected range
        fch = pd.DataFrame([dict(r) for r in db.get_strike_history(
            front_expiry, call_strike, "CALL", since_iso, db_path)])
        bch = pd.DataFrame([dict(r) for r in db.get_strike_history(
            back_expiry, call_strike, "CALL", since_iso, db_path)])
        fph = pd.DataFrame([dict(r) for r in db.get_strike_history(
            front_expiry, put_strike, "PUT", since_iso, db_path)])
        bph = pd.DataFrame([dict(r) for r in db.get_strike_history(
            back_expiry, put_strike, "PUT", since_iso, db_path)])

        # Need at least call history on both legs to draw the chart
        call_history_ready = not fch.empty and not bch.empty
        put_history_ready = not fph.empty and not bph.empty

        if call_history_ready or put_history_ready:
            fig_strike = go.Figure()

            if call_history_ready:
                call_merged = pd.merge(
                    fch[["timestamp", "iv"]].rename(columns={"iv": "f_call_iv"}),
                    bch[["timestamp", "iv"]].rename(columns={"iv": "b_call_iv"}),
                    on="timestamp", how="inner",
                )
                call_merged["call_ratio"] = call_merged["f_call_iv"] / call_merged["b_call_iv"]
                call_merged["timestamp"] = pd.to_datetime(call_merged["timestamp"], format="ISO8601")
                fig_strike.add_trace(go.Scatter(
                    x=call_merged["timestamp"], y=call_merged["f_call_iv"],
                    name=f"Front {call_strike:.0f}C IV",
                    line=dict(color="#2ecc71", width=1.5), yaxis="y1"
                ))
                fig_strike.add_trace(go.Scatter(
                    x=call_merged["timestamp"], y=call_merged["b_call_iv"],
                    name=f"Back {call_strike:.0f}C IV",
                    line=dict(color="#3498db", width=1.5), yaxis="y1"
                ))
                fig_strike.add_trace(go.Scatter(
                    x=call_merged["timestamp"], y=call_merged["call_ratio"],
                    name="Call IV Ratio (F/B)",
                    line=dict(color="#e74c3c", width=1.5, dash="solid"), yaxis="y2"
                ))

            if put_history_ready:
                put_merged = pd.merge(
                    fph[["timestamp", "iv"]].rename(columns={"iv": "f_put_iv"}),
                    bph[["timestamp", "iv"]].rename(columns={"iv": "b_put_iv"}),
                    on="timestamp", how="inner",
                )
                put_merged["put_ratio"] = put_merged["f_put_iv"] / put_merged["b_put_iv"]
                put_merged["timestamp"] = pd.to_datetime(put_merged["timestamp"], format="ISO8601")
                fig_strike.add_trace(go.Scatter(
                    x=put_merged["timestamp"], y=put_merged["f_put_iv"],
                    name=f"Front {put_strike:.0f}P IV",
                    line=dict(color="#2ecc71", width=1.5, dash="dot"), yaxis="y1"
                ))
                fig_strike.add_trace(go.Scatter(
                    x=put_merged["timestamp"], y=put_merged["b_put_iv"],
                    name=f"Back {put_strike:.0f}P IV",
                    line=dict(color="#3498db", width=1.5, dash="dot"), yaxis="y1"
                ))
                fig_strike.add_trace(go.Scatter(
                    x=put_merged["timestamp"], y=put_merged["put_ratio"],
                    name="Put IV Ratio (F/B)",
                    line=dict(color="#e74c3c", width=1.5, dash="dot"), yaxis="y2"
                ))

            fig_strike.update_layout(
                height=340,
                margin=dict(l=20, r=20, t=10, b=20),
                yaxis=dict(title="IV %", side="left"),
                yaxis2=dict(title="Ratio", side="right", overlaying="y", showgrid=False),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
                hovermode="x unified",
            )
            st.plotly_chart(fig_strike, use_container_width=True)
        else:
            st.info(
                f"Strike-specific history will appear here as the dashboard collects data "
                f"for {call_strike:.0f}C / {put_strike:.0f}P. Keep it running — "
                f"each 10s poll adds a data point."
            )
    else:
        st.markdown("#### Selected-Strike IV")
        st.caption("Enter call and put strikes in the left panel to see strike-specific IV history here.")

    # ── BOTTOM CHART — ATM IV ────────────────────────────────────────────────
    st.markdown("#### ATM IV  *(macro context — floating strike nearest spot)*")

    if not atm_merged.empty:
        fig_atm = go.Figure()
        fig_atm.add_trace(go.Scatter(
            x=atm_merged["timestamp"], y=atm_merged["front_iv"],
            name="Front ATM IV", line=dict(color="#2ecc71", width=1.5), yaxis="y1"
        ))
        fig_atm.add_trace(go.Scatter(
            x=atm_merged["timestamp"], y=atm_merged["back_iv"],
            name="Back ATM IV", line=dict(color="#3498db", width=1.5), yaxis="y1"
        ))
        fig_atm.add_trace(go.Scatter(
            x=atm_merged["timestamp"], y=atm_merged["iv_ratio"],
            name="IV Ratio (F/B)", line=dict(color="#e74c3c", width=1.5), yaxis="y2"
        ))
        fig_atm.update_layout(
            height=300,
            margin=dict(l=20, r=20, t=10, b=20),
            yaxis=dict(title="IV %", side="left"),
            yaxis2=dict(title="Ratio", side="right", overlaying="y", showgrid=False),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            hovermode="x unified",
        )
        st.plotly_chart(fig_atm, use_container_width=True)

        # ── Historical range stats (ATM ratio) ────────────────────────────────
        st.subheader("Historical Stats — ATM IV Ratio")
        stat_cols = st.columns(5)
        for col, (label, days) in zip(
            stat_cols,
            [("Today", 1), ("5 Days", 5), ("10 Days", 10), ("15 Days", 15), ("1 Month", 30)],
        ):
            p_since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            pf = pd.DataFrame([dict(r) for r in db.get_expiry_history(front_expiry, p_since, db_path)])
            pb = pd.DataFrame([dict(r) for r in db.get_expiry_history(back_expiry, p_since, db_path)])
            with col:
                st.caption(label)
                if not pf.empty and not pb.empty:
                    pm = pd.merge(
                        pf[["timestamp", "atm_iv"]].rename(columns={"atm_iv": "f"}),
                        pb[["timestamp", "atm_iv"]].rename(columns={"atm_iv": "b"}),
                        on="timestamp",
                    )
                    pm["ratio"] = pm["f"] / pm["b"]
                    rs = iv_engine.range_stats(pm["ratio"], ts.ratio)
                    st.markdown(
                        f"""<div style="font-size:0.85em">{rs.low:.4f}
                        <div style="background:linear-gradient(90deg,#444,#888);height:6px;
                        border-radius:3px;position:relative;margin:4px 0;">
                        <div style="position:absolute;left:{rs.position_pct}%;top:-3px;
                        width:12px;height:12px;background:#e74c3c;border-radius:50%;
                        transform:translateX(-50%);"></div></div>{rs.high:.4f}</div>""",
                        unsafe_allow_html=True,
                    )
                else:
                    st.caption("No data yet")

        warning = iv_engine.sample_size_warning(atm_merged["iv_ratio"])
        if warning:
            st.warning(warning)
    else:
        st.caption(
            "No ATM history yet — keep the dashboard running to accumulate data."
        )

# ---------------------------------------------------------------------------
# Trade Quality Score
# ---------------------------------------------------------------------------
st.subheader("Trade Quality Score")
near_front = chain_df[chain_df["expiry"] == front_expiry]
atm_row = near_front.iloc[(near_front["strike"] - spx_price).abs().argsort()[:1]]
liquidity = iv_engine.liquidity_score(
    atm_row["volume"].fillna(0).mean(),
    atm_row["open_interest"].fillna(0).mean(),
)
iv_pct = (
    iv_engine.percentile_rank(atm_merged["iv_ratio"], ts.ratio)
    if not atm_merged.empty
    else 50
)
theta_advantage = 50  # placeholder — Phase 3
score = iv_engine.trade_quality_score(iv_pct, liquidity, theta_advantage)
s1, s2, s3, s4 = st.columns(4)
s1.metric("Overall Score", f"{score:.0f} / 100")
s2.metric("IV Edge (ATM percentile)", f"{iv_pct:.0f}")
s3.metric("Liquidity", f"{liquidity:.0f}")
s4.metric("Theta Adv. (placeholder)", f"{theta_advantage:.0f}")

# ---------------------------------------------------------------------------
# Options chain table
# ---------------------------------------------------------------------------
st.subheader(f"Options Chain — {front_expiry}")
display_cols = ["strike", "side", "bid", "ask", "iv", "volume", "open_interest", "delta"]
chain_view = (
    chain_df[chain_df["expiry"] == front_expiry][display_cols]
    .sort_values("strike")
)
st.dataframe(chain_view, use_container_width=True, height=400)
