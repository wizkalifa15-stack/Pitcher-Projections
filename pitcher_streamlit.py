import streamlit as st
import sys
import io
from datetime import datetime
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path

# ── Load the projection engine ───────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def load_model():
    spec = spec_from_file_location(
        "pitcher_model",
        Path(__file__).parent / "from pybaseball import statcast_pitcher.py"
    )
    m = module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

model = load_model()

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Pitcher Strikeout Projections",
    page_icon="⚾",
    layout="centered"
)

st.title("⚾ Pitcher Strikeout Projections")
st.caption(f"Today (ET): {model.get_current_et().strftime('%A, %B %d %Y')}")

st.divider()

# ── Run button ────────────────────────────────────────────────────────────────
if st.button("▶ Run Projections", type="primary", use_container_width=True):

    status = st.empty()
    status.info("Fetching today's probable starters…")

    # Capture all print() output from the model
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf

    try:
        pitcher_opponents = model.get_starting_pitchers_today()
    except Exception as e:
        sys.stdout = old_stdout
        st.error(f"Failed to fetch schedule: {e}")
        st.stop()

    sys.stdout = old_stdout

    if not pitcher_opponents:
        status.warning("No probable starters found for today.")
        st.stop()

    status.success(f"Found {len(pitcher_opponents)} probable starters — running projections…")

    results = []

    for pitcher_id, opponent, pitcher_name in pitcher_opponents:
        display = pitcher_name if pitcher_name and not pitcher_name.startswith("ID:") else str(pitcher_id)

        buf = io.StringIO()
        sys.stdout = buf
        try:
            model.run_projection_for_pitcher(pitcher_id, opponent, pitcher_name)
        except Exception as e:
            sys.stdout = old_stdout
            results.append({"name": display, "opponent": opponent, "error": str(e)})
            continue
        sys.stdout = old_stdout

        output = buf.getvalue()

        # Parse projection values out of the printed output
        def _parse(label, text):
            for line in text.splitlines():
                if line.strip().startswith(label):
                    val = line.split(":")[-1].strip()
                    try:
                        return float(val)
                    except Exception:
                        return val
            return None

        results.append({
            "name": display,
            "opponent": opponent,
            "expected_k":   _parse("Expected Strikeouts", output),
            "avg_k":        _parse("Avg Strikeouts", output),
            "avg_ip":       _parse("Avg Innings", output),
            "avg_velo":     _parse("Avg Velocity", output),
            "prob_5":       _parse("K >= 5", output),
            "prob_6":       _parse("K >= 6", output),
            "prob_7":       _parse("K >= 7", output),
            "raw":          output,
        })

    status.empty()

    if not results:
        st.warning("No projections could be generated.")
        st.stop()

    # ── Display results ───────────────────────────────────────────────────────
    st.subheader("Projections")

    for r in results:
        with st.expander(f"**{r['name']}** vs {r['opponent']}", expanded=True):
            if "error" in r:
                st.error(r["error"])
                continue

            col1, col2, col3 = st.columns(3)
            col1.metric("Expected Ks",  f"{r['expected_k']:.2f}" if r['expected_k'] is not None else "—")
            col2.metric("Avg Ks (14d)", f"{r['avg_k']:.2f}"      if r['avg_k']       is not None else "—")
            col3.metric("Avg IP (14d)", f"{r['avg_ip']:.2f}"     if r['avg_ip']      is not None else "—")

            st.write("**Strikeout Probabilities**")
            p1, p2, p3 = st.columns(3)
            p1.metric("K ≥ 5", f"{r['prob_5']:.1%}" if r['prob_5'] is not None else "—")
            p2.metric("K ≥ 6", f"{r['prob_6']:.1%}" if r['prob_6'] is not None else "—")
            p3.metric("K ≥ 7", f"{r['prob_7']:.1%}" if r['prob_7'] is not None else "—")

            with st.expander("Full output log", expanded=False):
                st.code(r["raw"], language=None)
