#!/usr/bin/env python3
"""
HAL VTOL Hybrid Optimizer — Streamlit Dashboard
================================================
Interactive physics simulation + ML dispatch dashboard for the
HAL × IIT Indore Aerothon 2026 — Problem Statement 1.

Run:
    streamlit run dashboard.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from vtol_optimizer import (
    HALConstraints, SeriesHybridDesign, evaluate_design, isa_density_kg_m3
)
from vtol_optimizer.optimizer import DesignSpace, mission_dispatch_optimize

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="HAL VTOL Optimizer",
    page_icon="🚁",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Inter:wght@400;500;600;700&display=swap');

    .stApp { background: #0d1117; color: #e6edf3; font-family: 'Inter', sans-serif; }
    h1, h2, h3, h4 { color: #58a6ff !important; font-family: 'Inter', sans-serif; }

    /* Metric cards */
    div[data-testid="stMetric"] {
        background: #161b22;
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 12px 16px;
    }
    div[data-testid="stMetric"] label { color: #8b949e !important; font-size: 0.8rem; }
    div[data-testid="stMetric"] .metric-value { color: #58a6ff !important; font-family: 'JetBrains Mono', monospace; font-size: 1.4rem !important; }

    /* Code/equation boxes */
    .eq-box {
        background: #161b22;
        border-left: 3px solid #388bfd;
        border-radius: 4px;
        padding: 12px 18px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.85rem;
        color: #79c0ff;
        margin: 8px 0;
    }

    /* Pass/Fail badges */
    .pass { color: #3fb950; font-weight: 600; }
    .fail { color: #f85149; font-weight: 600; }

    /* Sidebar */
    section[data-testid="stSidebar"] { background: #010409; border-right: 1px solid #30363d; }

    /* Slider label override */
    .st-z3 .st-cx { color: #e6edf3; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ────────────────────────────────────────────────────────────────────
@st.cache_data
def run_sim(battery_kwh, fuel_kg, rotor_disk_area_m2, engine_load_fraction,
            cruise_altitude_m, loiter_speed_mps, loiter_minutes):
    constraints = HALConstraints()
    design = SeriesHybridDesign(
        battery_kwh=battery_kwh,
        fuel_kg=fuel_kg,
        rotor_disk_area_m2=rotor_disk_area_m2,
        engine_load_fraction=engine_load_fraction,
        cruise_altitude_m=cruise_altitude_m,
        loiter_speed_mps=loiter_speed_mps,
    )
    result = evaluate_design(constraints, design, loiter_seconds=loiter_minutes * 60)
    dispatch = mission_dispatch_optimize(constraints, design, result)
    return design, result, dispatch


def phase_chart(result):
    phases = [p.phase_name for p in result.phases]
    bus_kw = [p.required_bus_peak_kw for p in result.phases]
    batt_kw = [p.battery_peak_kw for p in result.phases]
    durations = [p.duration_s / 60 for p in result.phases]

    fig = make_subplots(rows=2, cols=1, subplot_titles=("Power Split (kW)", "State of Charge (%)"),
                        row_heights=[0.55, 0.45], vertical_spacing=0.12)

    colors = {"Bus": "#58a6ff", "Battery": "#3fb950", "Engine": "#f0883e"}

    for name, y in [("Bus", bus_kw), ("Battery", batt_kw)]:
        fig.add_trace(go.Bar(name=name, x=phases, y=y, marker_color=colors[name],
                             hovertemplate="%{x}<br>%{y:.1f} kW<extra></extra>"),
                      row=1, col=1)

    soc_series = [100.0] + [p.soc_final_pct for p in result.phases]

    fig.add_trace(go.Scatter(x=phases, y=soc_series,
                             mode="lines+markers", name="SOC",
                             line=dict(color="#bf5af2", width=3),
                             hovertemplate="SOC: %{y:.1f}%<extra></extra>"),
                  row=2, col=1)

    fig.update_layout(
        barmode="group", template="plotly_dark",
        paper_bgcolor="#0d1117", plot_bgcolor="#161b22",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=40, r=20, t=50, b=40),
        height=420,
    )
    fig.update_yaxes(title_text="kW", row=1, col=1, gridcolor="#21262d")
    fig.update_yaxes(title_text="%", row=2, col=1, gridcolor="#21262d", range=[0, 105])
    fig.update_xaxes(gridcolor="#21262d", row=1, col=1)
    fig.update_xaxes(gridcolor="#21262d", row=2, col=1)
    return fig


def pareto_chart():
    constraints = HALConstraints()
    space = DesignSpace()
    candidates = space.random_sampling(constraints, n=400)

    xs, ys, cols = [], [], []
    for d in candidates:
        r = evaluate_design(constraints, d, loiter_seconds=0)
        xs.append(r.takeoff_mass_kg)
        ys.append(r.max_endurance_h)
        cols.append("#3fb950" if r.completed_all_required_phases else "#f85149")

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=xs, y=ys, mode="markers",
        marker=dict(color=cols, size=6, opacity=0.7),
        hovertemplate="Mass: %{x:.0f} kg<br>Endurance: %{y:.2f} h<extra></extra>",
    ))
    fig.update_layout(
        title="Design Space — Mass vs Endurance (400 random samples)",
        xaxis_title="Takeoff Mass (kg)", yaxis_title="Endurance (h)",
        template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#161b22",
        height=380, margin=dict(l=40, r=20, t=50, b=40),
    )
    fig.update_xaxes(gridcolor="#21262d")
    fig.update_yaxes(gridcolor="#21262d")
    return fig


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🚁 **HAL VTOL Optimizer**")
    st.markdown("**HAL × IIT Indore Aerothon 2026**")
    st.markdown("Problem Statement 1 — Series-Hybrid")
    st.divider()

    st.markdown("### 🎛️ Design Variables")
    battery_kwh = st.slider("Battery capacity (kWh)", 5.0, 50.0, 34.0, 0.5,
                            help="Higher capacity → more electric reserve, heavier aircraft")
    fuel_kg = st.slider("Fuel mass (kg)", 10.0, 200.0, 145.0, 1.0,
                        help="More fuel → longer range, heavier aircraft")
    rotor_disk_area_m2 = st.slider("Rotor disk area (m²)", 15.0, 80.0, 45.0, 0.5,
                                   help="Larger disk → lower disk loading, more thrust per kW")
    engine_load_fraction = st.slider("Engine load fraction", 0.50, 1.00, 0.92, 0.01,
                                   help="Fraction of 60 kW turboshaft rating used in cruise")
    cruise_altitude_m = st.slider("Cruise altitude (m)", 3000, 10000, 6000, 100,
                                  help="Higher → thinner air, less drag but lower engine density")
    loiter_speed_mps = st.slider("Loiter speed (m/s)", 30.0, 80.0, 51.0, 0.5,
                                  help="~100 kt = 51.4 m/s")
    loiter_minutes = st.slider("Loiter duration (min)", 0, 60, 30, 1)

    st.divider()
    st.markdown("### ℹ️ **HAL Constraints**")
    st.markdown("""
    | Parameter | Value |
    |---|---|
    | MTOW | 1,000 kg |
    | Payload | 200 kg |
    | Cruise speed | 250 km/h |
    | Altitude | 3–10 km |
    | Min loiter | 30 min |
    | Turboshaft | 60 kW |
    | Battery max | 50 kWh |
    """)


# ── Main ───────────────────────────────────────────────────────────────────────
st.title("HAL VTOL Series-Hybrid Optimizer")

tab_sim, tab_pareto, tab_ml, tab_eq = st.tabs(
    ["📐 Mission Simulator", "📊 Pareto Front", "🤖 ML Dispatcher", "📋 Equations"]
)

with tab_sim:
    design, result, dispatch = run_sim(
        battery_kwh, fuel_kg, rotor_disk_area_m2,
        engine_load_fraction, cruise_altitude_m,
        loiter_speed_mps, loiter_minutes,
    )

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Takeoff Mass", f"{result.takeoff_mass_kg:.1f} kg",
                  f"HAL MTOW = 1000 kg")
    with col2:
        st.metric("Max Endurance", f"{result.max_endurance_h:.2f} h",
                  f"{result.max_endurance_h*60:.0f} min")
    with col3:
        total_fuel = sum(p.fuel_consumed_kg for p in result.phases)
        st.metric("Fuel Burned", f"{total_fuel:.1f} kg",
                  f"{total_fuel/result.takeoff_mass_kg*100:.1f}% of mass")
    with col4:
        st.metric("Mission", "✅ Complete" if result.completed_all_required_phases else "❌ Failed")

    st.plotly_chart(phase_chart(result), width='stretch')

    with st.expander("🔍 Phase-by-phase breakdown"):
        for p in result.phases:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Phase", p.phase_name)
            c2.metric("Duration", f"{p.duration_s:.0f} s")
            c3.metric("Bus Power", f"{p.required_bus_peak_kw:.1f} kW")
            c4.metric("Battery Draw", f"{p.battery_drawn_kwh:.2f} kWh")

    st.divider()
    st.markdown("#### 🪨 Mass Budget")
    mass_data = {
        "Component": ["Payload", "Battery", "Structure", "Fuel System", "Systems/Margin"],
        "Mass (kg)": [result.payload_kg, result.battery_mass_kg, result.structure_mass_kg,
                      result.fuel_mass_kg, 60.0],
    }
    fig_mass = px.bar(mass_data, x="Component", y="Mass (kg)",
                      color="Mass (kg)", color_continuous_scale="blues",
                      template="plotly_dark", height=280)
    fig_mass.update_layout(
        paper_bgcolor="#0d1117", plot_bgcolor="#161b22",
        margin=dict(l=20, r=20, t=20, b=40),
    )
    st.plotly_chart(fig_mass, width='stretch')

    st.divider()
    st.markdown("#### ⚡ Optimal Dispatch (per phase)")
    disp_data = [{"Phase": k, "Engine (kW)": v["engine_kw"],
                  "Battery (kW)": v["battery_kw"], "Fuel Flow (kg/h)": v["fuel_flow_kg_h"]}
                 for k, v in dispatch.items()]
    st.dataframe(disp_data, width='stretch', hide_index=True)


with tab_pareto:
    st.plotly_chart(pareto_chart(), width='stretch')
    st.info(
        "Each dot = one randomly sampled design. "
        "**Green** = meets all HAL constraints; **Red** = violates at least one. "
        "The Pareto front identifies designs that are not dominated — "
        "no other design has both higher endurance AND lower mass."
    )

with tab_ml:
    st.markdown("#### 🤖 ML-Based Power Dispatcher")
    st.markdown("""
    The **ML Power Dispatcher** is a supervised neural-network (MLP) that learns
    the optimal battery / engine power split from physics simulation data — then
    generalises to unseen flight conditions in **real time**, with near-optimal
    accuracy at a fraction of the cost of traditional optimisation.
    """)
    st.latex(r"""
    \text{Input: } \mathbf{x} = [phase\_id,\; altitude,\; speed,\; SOC,\; mass,\; T_{batt}]
    """)
    st.latex(r"""
    \hat{\mathbf{y}} = MLP(\mathbf{x}) = [P_{engine}^{opt},\; P_{battery}^{opt}]
    """)
    st.latex(r"""
    \mathcal{L} = \frac{1}{N}\sum_{(x,y)\in\mathcal{D}}
                   \bigl\| \hat{y} - y \bigr\|_2^2
    """)

    st.markdown("""
    **Training pipeline:**
    1. Generate ~2,000 simulation samples spanning the design space
    2. Record optimal dispatch from DP at each operating point
    3. Train MLPRegressor (scikit-learn) on (x → y) pairs
    4. Deploy as real-time setpoint predictor for the ECU
    """)
    st.code("""
    from vtol_optimizer.ml_dispatcher import generate_training_data, train_dispatcher
    generate_training_data(HALConstraints(), n_samples=2000)   # → dispatch_data.csv
    train_dispatcher("dispatch_data.csv")                        # → dispatcher_model.pkl
    """, language="python")

    st.warning(
        "⚠️ The ML module requires `numpy`, `pandas`, and `scikit-learn`. "
        "Install dependencies with: `pip install -r requirements.txt`"
    )

with tab_eq:
    st.markdown("#### 🌐 ISA Atmosphere")
    st.latex(r"T(h) = T_0 - L \cdot h \quad where \quad T_0 = 288.15\text{ K},\; L = 0.0065\text{ K/m}")
    st.latex(r"P(h) = P_0 \left(1 - \frac{L h}{T_0}\right)^{\frac{g M}{R L}} \quad"
             r"P_0 = 101325\text{ Pa}")
    st.latex(r"\rho(h) = \frac{P(h)}{R \cdot T(h)}, \quad R = 287.05\text{ J/(kg·K)}")
    st.code("# Example\nrho_6km = isa_density_kg_m3(6000)  # ≈ 0.66 kg/m³", language="python")

    st.markdown("#### 💨 Aerodynamics & Power")
    st.latex(r"D = \frac{1}{2}\,\rho\,v^2\,C_{D}A")
    st.latex(r"P_{bus} = \frac{D \cdot v}{\eta_{prop}} + \frac{T_{rotor}\cdot v}{\eta_{rotor}}")
    st.latex(r"P_{engine}(t) = \min\!\bigl(0.92 \times 60\text{ kW},\; P_{req}(t)\bigr)")
    st.latex(r"P_{battery}(t) = \max\!\bigl(0,\; P_{req}(t) - P_{engine}(t)\bigr)")

    st.markdown("#### 🔋 Battery Model")
    st.latex(r"\text{SOC}_{k+1} = \text{SOC}_k - \frac{P_{batt}(t_k)\;\Delta t}{E_{batt}}")
    st.latex(r"m_{batt} = \frac{E_{batt}}{250}\quad\text{(cell-level specific energy)}")

    st.markdown("#### ⛽ Mass Budget")
    st.latex(r"M_{TOW} = m_{payload} + m_{batt} + m_{structure} + m_{fuel} + m_{systems}")
    st.latex(r"m_{batt} = \frac{E_{batt}}{250}\quad\text{[kg]}\quad (\text{cell-level specific energy}=250\text{ Wh/kg})")
    st.latex(r"m_{structure} = 280 + A_{rotor} \times 3.8\quad\text{[kg]}")
    st.latex(r"m_{fuel\;system} = m_{fuel} + 8\quad\text{[kg]  (fixed tank mass independent of fuel)}")

    st.markdown("#### ⏱️ Endurance")
    st.latex(r"E_{max} = \frac{\eta_{batt}\;E_{batt}\;(1-\text{SOC}_{min})}{P_{loiter}}\quad\text{[h]}")
