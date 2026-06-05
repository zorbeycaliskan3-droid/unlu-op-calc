"""
Option Pricing Calculator
- Black-Scholes pricing + Greeks
- IB TWS integration (ibapi)
- P&L payoff diagram (matplotlib)
- Volatility surface visualization (matplotlib)
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import numpy as np
from scipy.stats import norm
import time
from datetime import datetime
import warnings
import json
import os
import uuid

# --- matplotlib embedding ---
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from mpl_toolkits.mplot3d import Axes3D          # noqa: F401 (registers 3-D projection)

# --- IB API ---
try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract
    IB_AVAILABLE = True
except ImportError:
    IB_AVAILABLE = False

warnings.filterwarnings("ignore")


# ──────────────────────────────────────────────────────────────────────────────
#  IB Application wrapper
# ──────────────────────────────────────────────────────────────────────────────
if IB_AVAILABLE:
    class IBApp(EWrapper, EClient):
        def __init__(self):
            EClient.__init__(self, self)
            self.connected = False
            self.market_data = {}
            self.historical_data = {}
            self._next_req_id = 10

        # ---- EWrapper callbacks ----
        def error(self, reqId, errorCode, errorString, *args):
            # Suppress harmless info codes
            if errorCode in (2104, 2106, 2158, 2176):
                return
            print(f"[IB Error {errorCode}] reqId={reqId}: {errorString}")

        def nextValidId(self, orderId: int):
            self.connected = True
            print(f"Connected to IB TWS – next valid orderId: {orderId}")

        def historicalData(self, reqId, bar):
            self.historical_data.setdefault(reqId, []).append({
                "date":   bar.date,
                "open":   bar.open,
                "high":   bar.high,
                "low":    bar.low,
                "close":  bar.close,
                "volume": bar.volume,
            })

        def historicalDataEnd(self, reqId, start, end):
            print(f"[IB] Historical data done – reqId={reqId}  ({start} → {end})")

else:
    # Stub so the rest of the code can still reference IBApp
    class IBApp:  # type: ignore
        def __init__(self):
            self.connected = False
            self.historical_data = {}
            self.market_data = {}

        def connect(self, *a, **kw):      pass
        def disconnect(self):             pass
        def run(self):                    pass
        def reqHistoricalData(self, *a, **kw): pass


# ──────────────────────────────────────────────────────────────────────────────
#  Main Application
# ──────────────────────────────────────────────────────────────────────────────
class OptionPricingCalculator:
    # ------------------------------------------------------------------ init --
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Option Pricing Calculator")
        self.root.geometry("1350x900")
        self.root.configure(bg="#0f0f14")

        # IB state
        self.ib_app = IBApp()
        self.ib_connected = False

        # Cached market values (not the tk.Vars – avoids destroying them)
        self._spot_from_ib: float | None = None
        self._vol_from_ib:  float | None = None

        # ── Input tk variables ──
        self.current_price   = tk.DoubleVar(value=100.0)
        self.strike_price    = tk.DoubleVar(value=100.0)
        self.time_to_expiry  = tk.DoubleVar(value=30.0)   # days
        self.risk_free_rate  = tk.DoubleVar(value=0.05)
        self.volatility      = tk.DoubleVar(value=0.20)
        self.option_type     = tk.StringVar(value="call")

        # ── Output tk variables (current) ──
        self.current_delta_var = tk.StringVar(value="-")
        self.current_gamma_var = tk.StringVar(value="-")
        self.current_vega_var  = tk.StringVar(value="-")
        self.current_theta_var = tk.StringVar(value="-")
        self.current_rho_var   = tk.StringVar(value="-")

        # ── Scenario inputs ──
        self.new_spot_price_var = tk.StringVar()
        self.new_volatility_var = tk.StringVar()

        # ── Status ──
        self.status_var = tk.StringVar(value="Ready.")

        # ── OTC Book state ──
        self.otc_positions: list = []
        self._otc_editing_id: str | None = None
        self._otc_book_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "otc_book.json"
        )

        self._setup_styles()
        self._setup_ui()

    # --------------------------------------------------------------- styles --
    def _setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")

        BG     = "#0f0f14"
        PANEL  = "#1a1a24"
        BORDER = "#2a2a3a"
        FG     = "#e8e8f0"
        ACC    = "#7c6af7"   # purple accent
        ACC2   = "#4ecdc4"   # teal accent
        BTN_BG = "#2a2a3a"

        style.configure(".",            background=BG,    foreground=FG,
                         font=("Courier New", 10))
        style.configure("TFrame",       background=BG)
        style.configure("TLabel",       background=BG,    foreground=FG,
                         font=("Courier New", 10))
        style.configure("TEntry",       fieldbackground=PANEL, foreground=FG,
                         insertcolor=FG, bordercolor=BORDER, font=("Courier New", 10))
        style.configure("TRadiobutton", background=BG,    foreground=FG,
                         font=("Courier New", 10))
        style.configure("TButton",      background=BTN_BG, foreground=FG,
                         font=("Courier New", 10, "bold"), borderwidth=1,
                         relief="flat", padding=6)
        style.map("TButton",
                  background=[("active", ACC), ("pressed", "#5a4fd0")],
                  foreground=[("active", "#ffffff")])

        style.configure("Accent.TButton", background=ACC, foreground="#ffffff",
                         font=("Courier New", 10, "bold"), padding=6)
        style.map("Accent.TButton",
                  background=[("active", "#6a5ae0"), ("pressed", "#5a4fd0")])

        style.configure("TLabelframe",       background=BG,    foreground=ACC,
                         bordercolor=BORDER, font=("Courier New", 10, "bold"))
        style.configure("TLabelframe.Label", background=BG,    foreground=ACC,
                         font=("Courier New", 10, "bold"))

        # store colours for use in widget code
        self._c = dict(bg=BG, panel=PANEL, border=BORDER, fg=FG,
                       acc=ACC, acc2=ACC2, btn=BTN_BG)

    # ------------------------------------------------------------------ UI --
    def _setup_ui(self):
        c = self._c

        # Root grid
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        # ── Header ──
        hdr = tk.Frame(self.root, bg=c["panel"], height=56)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.columnconfigure(1, weight=1)
        tk.Label(hdr, text="◈  OPTION PRICING TERMINAL",
                 bg=c["panel"], fg=c["acc"],
                 font=("Courier New", 16, "bold")).pack(side="left", padx=20, pady=12)
        tk.Label(hdr, text="Black-Scholes · Greeks · Scenarios · Vol Surface · OTC Book",
                 bg=c["panel"], fg="#555577",
                 font=("Courier New", 9)).pack(side="left", pady=12)

        # ── Main 3-column layout ──
        body = tk.Frame(self.root, bg=c["bg"])
        body.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        body.columnconfigure(0, weight=0, minsize=310)
        body.columnconfigure(1, weight=0, minsize=280)
        body.columnconfigure(2, weight=1)
        body.rowconfigure(0, weight=1)

        left_col   = tk.Frame(body, bg=c["bg"])
        middle_col = tk.Frame(body, bg=c["bg"])
        right_col  = tk.Frame(body, bg=c["bg"])
        left_col  .grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        middle_col.grid(row=0, column=1, sticky="nsew", padx=(0, 6))
        right_col .grid(row=0, column=2, sticky="nsew")

        # ── Status bar ──
        sb = tk.Frame(self.root, bg="#12121a", height=24)
        sb.grid(row=2, column=0, sticky="ew")
        tk.Label(sb, textvariable=self.status_var,
                 bg="#12121a", fg="#445566",
                 font=("Courier New", 8), anchor="w").pack(
                     side="left", padx=10, pady=2)

        # ─── Left column ───
        self._build_connection_panel(left_col)
        self._build_market_data_panel(left_col)
        self._build_inputs_panel(left_col)

        # ─── Middle column ───
        self._build_current_results_panel(middle_col)
        self._build_scenario_panel(middle_col)
        self._build_new_results_panel(middle_col)

        # ─── Right column (charts) ───
        self._build_charts_panel(right_col)

    # ─────────────────────── LEFT COLUMN PANELS ──────────────────────────────

    def _build_connection_panel(self, parent):
        c = self._c
        frm = ttk.LabelFrame(parent, text="IB TWS CONNECTION", padding=10)
        frm.pack(fill="x", pady=(0, 6))

        row0 = tk.Frame(frm, bg=c["bg"])
        row0.pack(fill="x", pady=(0, 6))

        tk.Label(row0, text="Host:", bg=c["bg"], fg=c["fg"],
                 font=("Courier New", 9)).pack(side="left")
        self.host_var = tk.StringVar(value="127.0.0.1")
        tk.Entry(row0, textvariable=self.host_var, width=14,
                 bg=c["panel"], fg=c["fg"], insertbackground=c["fg"],
                 relief="flat", font=("Courier New", 9)).pack(side="left", padx=(4, 12))
        tk.Label(row0, text="Port:", bg=c["bg"], fg=c["fg"],
                 font=("Courier New", 9)).pack(side="left")
        self.port_var = tk.StringVar(value="7497")
        tk.Entry(row0, textvariable=self.port_var, width=6,
                 bg=c["panel"], fg=c["fg"], insertbackground=c["fg"],
                 relief="flat", font=("Courier New", 9)).pack(side="left", padx=(4, 0))

        row1 = tk.Frame(frm, bg=c["bg"])
        row1.pack(fill="x")
        self.connect_btn = ttk.Button(row1, text="Connect",
                                      command=self._connect_ib,
                                      style="Accent.TButton")
        self.connect_btn.pack(side="left", padx=(0, 8))
        self.disconnect_btn = ttk.Button(row1, text="Disconnect",
                                         command=self._disconnect_ib,
                                         state="disabled")
        self.disconnect_btn.pack(side="left")

        self.conn_status_lbl = tk.Label(frm,
                                        text="● DISCONNECTED",
                                        bg=c["bg"], fg="#cc4444",
                                        font=("Courier New", 9, "bold"))
        self.conn_status_lbl.pack(anchor="w", pady=(6, 0))

        if not IB_AVAILABLE:
            tk.Label(frm, text="⚠ ibapi not installed – IB features disabled",
                     bg=c["bg"], fg="#cc8844",
                     font=("Courier New", 8)).pack(anchor="w")

    def _build_market_data_panel(self, parent):
        c = self._c
        frm = ttk.LabelFrame(parent, text="MARKET DATA FETCH", padding=10)
        frm.pack(fill="x", pady=(0, 6))

        row = tk.Frame(frm, bg=c["bg"])
        row.pack(fill="x", pady=(0, 6))
        tk.Label(row, text="Ticker:", bg=c["bg"], fg=c["fg"],
                 font=("Courier New", 9)).pack(side="left")
        self.ticker_var = tk.StringVar(value="AAPL")
        tk.Entry(row, textvariable=self.ticker_var, width=8,
                 bg=c["panel"], fg=c["fg"], insertbackground=c["fg"],
                 relief="flat", font=("Courier New", 10, "bold")).pack(
                     side="left", padx=(6, 10))
        self.fetch_btn = ttk.Button(row, text="Fetch Data",
                                    command=self._fetch_market_data,
                                    state="disabled")
        self.fetch_btn.pack(side="left")

        self.fetched_price_var = tk.StringVar(value="Spot:  --")
        self.fetched_vol_var   = tk.StringVar(value="IV:    --")
        tk.Label(frm, textvariable=self.fetched_price_var,
                 bg=c["bg"], fg=c["acc2"],
                 font=("Courier New", 9)).pack(anchor="w")
        tk.Label(frm, textvariable=self.fetched_vol_var,
                 bg=c["bg"], fg=c["acc2"],
                 font=("Courier New", 9)).pack(anchor="w")

    def _build_inputs_panel(self, parent):
        c = self._c
        frm = ttk.LabelFrame(parent, text="PARAMETERS", padding=10)
        frm.pack(fill="x", pady=(0, 6))

        fields = [
            ("Spot Price  (S)",         self.current_price),
            ("Strike Price (K)",        self.strike_price),
            ("Days to Expiry (T)",      self.time_to_expiry),
            ("Risk-free Rate (r)",      self.risk_free_rate),
            ("Volatility σ (decimal)",  self.volatility),
        ]
        for label, var in fields:
            row = tk.Frame(frm, bg=c["bg"])
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, width=22, anchor="w",
                     bg=c["bg"], fg=c["fg"],
                     font=("Courier New", 9)).pack(side="left")
            tk.Entry(row, textvariable=var, width=12,
                     bg=c["panel"], fg=c["acc2"], insertbackground=c["fg"],
                     relief="flat", font=("Courier New", 10, "bold")).pack(
                         side="left", padx=(6, 0))

        # Option type
        sep = tk.Frame(frm, bg=c["border"], height=1)
        sep.pack(fill="x", pady=8)
        ot_row = tk.Frame(frm, bg=c["bg"])
        ot_row.pack(fill="x")
        tk.Label(ot_row, text="Option Type:", bg=c["bg"], fg=c["fg"],
                 font=("Courier New", 9)).pack(side="left")
        for val, lbl in (("call", "CALL"), ("put", "PUT")):
            ttk.Radiobutton(ot_row, text=lbl, value=val,
                            variable=self.option_type).pack(side="left", padx=8)

        # Calculate button
        ttk.Button(frm, text="▶  CALCULATE",
                   command=self._on_calculate,
                   style="Accent.TButton").pack(fill="x", pady=(12, 0))

    # ─────────────────────── MIDDLE COLUMN PANELS ────────────────────────────

    def _build_current_results_panel(self, parent):
        c = self._c
        frm = ttk.LabelFrame(parent, text="CURRENT RESULTS", padding=10)
        frm.pack(fill="x", pady=(0, 6))

        # Big premium display
        self.current_premium_var = tk.StringVar(value="$-.--")
        tk.Label(frm, textvariable=self.current_premium_var,
                 bg=c["bg"], fg=c["acc"],
                 font=("Courier New", 26, "bold")).pack(anchor="w")

        sep = tk.Frame(frm, bg=c["border"], height=1)
        sep.pack(fill="x", pady=6)

        greek_pairs = [
            ("Delta",         self.current_delta_var),
            ("Gamma",         self.current_gamma_var),
            ("Vega",          self.current_vega_var),
            ("Theta/day",     self.current_theta_var),
            ("Rho",           self.current_rho_var),
        ]
        for name, var in greek_pairs:
            row = tk.Frame(frm, bg=c["bg"])
            row.pack(fill="x", pady=2)
            tk.Label(row, text=name, width=10, anchor="w",
                     bg=c["bg"], fg="#666688",
                     font=("Courier New", 9)).pack(side="left")
            tk.Label(row, textvariable=var, anchor="e",
                     bg=c["bg"], fg=c["fg"],
                     font=("Courier New", 10, "bold")).pack(side="right")

    def _build_scenario_panel(self, parent):
        c = self._c
        frm = ttk.LabelFrame(parent, text="SCENARIO ANALYSIS", padding=10)
        frm.pack(fill="x", pady=(0, 6))

        for label, var in (
            ("New Spot Price", self.new_spot_price_var),
            ("New Volatility", self.new_volatility_var),
        ):
            row = tk.Frame(frm, bg=c["bg"])
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, width=14, anchor="w",
                     bg=c["bg"], fg=c["fg"],
                     font=("Courier New", 9)).pack(side="left")
            tk.Entry(row, textvariable=var, width=12,
                     bg=c["panel"], fg=c["acc2"], insertbackground=c["fg"],
                     relief="flat", font=("Courier New", 10, "bold")).pack(
                         side="left", padx=(6, 0))

        self.analyze_btn = ttk.Button(frm, text="▶  ANALYZE SCENARIO",
                                      command=self._analyze_scenario)
        self.analyze_btn.pack(fill="x", pady=(10, 0))

    def _build_new_results_panel(self, parent):
        c = self._c
        frm = ttk.LabelFrame(parent, text="SCENARIO RESULTS", padding=10)
        frm.pack(fill="x", pady=(0, 6))

        self.new_premium_var = tk.StringVar(value="$-.--")
        tk.Label(frm, textvariable=self.new_premium_var,
                 bg=c["bg"], fg=c["acc2"],
                 font=("Courier New", 26, "bold")).pack(anchor="w")

        sep = tk.Frame(frm, bg=c["border"], height=1)
        sep.pack(fill="x", pady=6)

        self.new_delta_var = tk.StringVar(value="-")
        self.new_gamma_var = tk.StringVar(value="-")
        self.new_vega_var  = tk.StringVar(value="-")
        self.new_theta_var = tk.StringVar(value="-")
        self.new_rho_var   = tk.StringVar(value="-")

        for name, var in (
            ("Delta",     self.new_delta_var),
            ("Gamma",     self.new_gamma_var),
            ("Vega",      self.new_vega_var),
            ("Theta/day", self.new_theta_var),
            ("Rho",       self.new_rho_var),
        ):
            row = tk.Frame(frm, bg=c["bg"])
            row.pack(fill="x", pady=2)
            tk.Label(row, text=name, width=10, anchor="w",
                     bg=c["bg"], fg="#666688",
                     font=("Courier New", 9)).pack(side="left")
            tk.Label(row, textvariable=var, anchor="e",
                     bg=c["bg"], fg=c["fg"],
                     font=("Courier New", 10, "bold")).pack(side="right")

    # ─────────────────────── RIGHT COLUMN (CHARTS) ───────────────────────────

    def _build_charts_panel(self, parent):
        c = self._c
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        # Tab bar
        tab_bar = tk.Frame(parent, bg=c["panel"])
        tab_bar.pack(fill="x")

        self._chart_frames: dict[str, tk.Frame] = {}
        self._tab_btns: dict[str, tk.Button] = {}

        chart_area = tk.Frame(parent, bg=c["bg"])
        chart_area.pack(fill="both", expand=True)
        chart_area.columnconfigure(0, weight=1)
        chart_area.rowconfigure(0, weight=1)

        for name in ("P&L PAYOFF", "VOL SURFACE", "STRATEGIES", "OTC BOOK"):
            frame = tk.Frame(chart_area, bg=c["bg"])
            frame.grid(row=0, column=0, sticky="nsew")
            self._chart_frames[name] = frame

            btn = tk.Button(tab_bar, text=name,
                            bg=c["panel"], fg="#666688",
                            font=("Courier New", 9, "bold"),
                            relief="flat", padx=14, pady=6,
                            command=lambda n=name: self._show_chart_tab(n))
            btn.pack(side="left")
            self._tab_btns[name] = btn

        self._build_payoff_chart(self._chart_frames["P&L PAYOFF"])
        self._build_vol_surface_chart(self._chart_frames["VOL SURFACE"])
        self._build_strategy_chart(self._chart_frames["STRATEGIES"])
        self._build_otc_book_tab(self._chart_frames["OTC BOOK"])
        self._show_chart_tab("P&L PAYOFF")

    def _show_chart_tab(self, name: str):
        c = self._c
        for n, frame in self._chart_frames.items():
            frame.lower()
        self._chart_frames[name].lift()
        for n, btn in self._tab_btns.items():
            btn.config(fg=c["acc"] if n == name else "#666688",
                       bg=c["btn"] if n == name else c["panel"])

    # ── P&L payoff chart ──
    def _build_payoff_chart(self, parent):
        c = self._c
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        ctrl = tk.Frame(parent, bg=c["bg"])
        ctrl.pack(fill="x", padx=8, pady=6)
        ttk.Button(ctrl, text="↻ Refresh Chart",
                   command=self._refresh_payoff).pack(side="left")
        tk.Label(ctrl, text="  Premium paid:",
                 bg=c["bg"], fg=c["fg"],
                 font=("Courier New", 9)).pack(side="left", padx=(12, 4))
        self.prem_paid_var = tk.StringVar(value="0.00")
        tk.Entry(ctrl, textvariable=self.prem_paid_var, width=8,
                 bg=c["panel"], fg=c["acc2"], insertbackground=c["fg"],
                 relief="flat", font=("Courier New", 9)).pack(side="left")

        # Chart frame to hold both canvas and toolbar
        chart_container = tk.Frame(parent, bg=c["bg"])
        chart_container.pack(fill="both", expand=True, padx=4, pady=4)

        self._payoff_fig, self._payoff_ax = plt.subplots(figsize=(6, 4))
        self._payoff_fig.patch.set_facecolor(c["bg"])
        self._payoff_ax.set_facecolor(c["panel"])
        self._payoff_canvas = FigureCanvasTkAgg(self._payoff_fig, master=chart_container)
        
        # Add matplotlib navigation toolbar for zoom/pan
        from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk
        toolbar = NavigationToolbar2Tk(self._payoff_canvas, chart_container)
        toolbar.update()
        # Style the toolbar to match dark theme
        toolbar.config(bg=c["panel"])
        for child in toolbar.winfo_children():
            try:
                child.config(bg=c["panel"], fg=c["fg"])
            except:
                pass
        
        self._payoff_canvas.get_tk_widget().pack(fill="both", expand=True)
        self._refresh_payoff()

    def _refresh_payoff(self):
        c = self._c
        ax = self._payoff_ax
        ax.clear()

        try:
            S     = float(self.current_price.get())
            K     = float(self.strike_price.get())
            T_day = float(self.time_to_expiry.get())
            r     = float(self.risk_free_rate.get())
            sig   = float(self.volatility.get())
            opt   = self.option_type.get()
            prem  = float(self.prem_paid_var.get() or 0)
        except ValueError:
            ax.set_title("Enter valid parameters first",
                         color=c["fg"], fontsize=10)
            self._payoff_canvas.draw()
            return

        T = max(T_day / 365.0, 1e-6)
        S_range = np.linspace(S * 0.5, S * 1.5, 300)

        # Expiry payoff
        if opt == "call":
            payoff = np.maximum(S_range - K, 0) - prem
        else:
            payoff = np.maximum(K - S_range, 0) - prem

        # Today's theoretical value
        today_vals = np.array([
            self._black_scholes_price(s, K, T, r, sig, opt)[0] - prem
            for s in S_range
        ])

        ax.axhline(0, color=c["border"], linewidth=0.8, linestyle="--")
        ax.axvline(S, color="#555577", linewidth=1, linestyle=":")
        ax.axvline(K, color=c["acc"], linewidth=1.2, linestyle="--", alpha=0.7,
                   label=f"Strike K={K:.1f}")

        ax.fill_between(S_range, payoff, 0,
                        where=(payoff >= 0), alpha=0.15, color="#4ecdc4")
        ax.fill_between(S_range, payoff, 0,
                        where=(payoff < 0),  alpha=0.15, color="#cc4444")

        ax.plot(S_range, payoff,     color=c["acc2"],  linewidth=2,
                label="At expiry")
        ax.plot(S_range, today_vals, color=c["acc"],   linewidth=1.8,
                linestyle="--", label="Today (BS)")

        ax.set_xlabel("Spot Price", color=c["fg"], fontsize=9)
        ax.set_ylabel("P&L",        color=c["fg"], fontsize=9)
        ax.set_title(f"{opt.upper()} Payoff  ·  K={K:.2f}  T={T_day:.0f}d  σ={sig:.1%}",
                     color=c["fg"], fontsize=10)
        ax.tick_params(colors=c["fg"], labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor(c["border"])
        leg = ax.legend(fontsize=8, facecolor=c["panel"], labelcolor=c["fg"],
                        edgecolor=c["border"])
        self._payoff_fig.tight_layout()
        self._payoff_canvas.draw()

    # ── Volatility surface chart ──
    def _build_vol_surface_chart(self, parent):
        c = self._c
        ctrl = tk.Frame(parent, bg=c["bg"])
        ctrl.pack(fill="x", padx=8, pady=6)
        ttk.Button(ctrl, text="↻ Refresh Surface",
                   command=self._refresh_vol_surface).pack(side="left")

        # Chart container
        chart_container = tk.Frame(parent, bg=c["bg"])
        chart_container.pack(fill="both", expand=True, padx=4, pady=4)

        self._vol_fig = plt.figure(figsize=(6, 4))
        self._vol_fig.patch.set_facecolor(c["bg"])
        self._vol_canvas = FigureCanvasTkAgg(self._vol_fig, master=chart_container)
        
        # Add matplotlib navigation toolbar
        from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk
        toolbar = NavigationToolbar2Tk(self._vol_canvas, chart_container)
        toolbar.update()
        # Style toolbar
        toolbar.config(bg=c["panel"])
        for child in toolbar.winfo_children():
            try:
                child.config(bg=c["panel"], fg=c["fg"])
            except:
                pass
        
        self._vol_canvas.get_tk_widget().pack(fill="both", expand=True)
        self._refresh_vol_surface()

    def _refresh_vol_surface(self):
        c = self._c
        self._vol_fig.clear()
        ax = self._vol_fig.add_subplot(111, projection="3d")
        ax.set_facecolor(c["panel"])
        self._vol_fig.patch.set_facecolor(c["bg"])

        try:
            S   = float(self.current_price.get())
            sig = float(self.volatility.get())
            r   = float(self.risk_free_rate.get())
        except ValueError:
            ax.set_title("Enter valid parameters first",
                         color=c["fg"], fontsize=10)
            self._vol_canvas.draw()
            return

        # ── Stylised vol smile surface ──
        # Moneyness grid: K/S from 0.7 to 1.3
        moneyness = np.linspace(0.7, 1.3, 25)
        expiries   = np.linspace(7, 365, 25)          # days
        M, E = np.meshgrid(moneyness, expiries)

        # Parametric smile — same coefficients used by the OTC MTM engine
        skew    = self._SURF_SKEW  * (M - 1.0)
        smile   = self._SURF_SMILE * (M - 1.0) ** 2
        term_st = self._SURF_TERM  * (1 - np.exp(-E / self._SURF_DECAY))
        IV = sig + skew + smile + term_st
        IV = np.clip(IV, 0.01, 1.5)

        surf = ax.plot_surface(M, E, IV,
                               cmap="plasma",
                               alpha=0.88,
                               linewidth=0, antialiased=True)
        self._vol_fig.colorbar(surf, ax=ax, shrink=0.5, aspect=10,
                               label="Implied Vol")
        ax.set_xlabel("Moneyness K/S", color=c["fg"], fontsize=8, labelpad=6)
        ax.set_ylabel("Days to Exp",   color=c["fg"], fontsize=8, labelpad=6)
        ax.set_zlabel("Implied Vol",   color=c["fg"], fontsize=8, labelpad=6)
        ax.set_title("Vol Surface (Parametric Smile)",
                     color=c["fg"], fontsize=10, pad=10)
        ax.tick_params(colors=c["fg"], labelsize=7)
        ax.xaxis.pane.fill = False
        ax.yaxis.pane.fill = False
        ax.zaxis.pane.fill = False
        ax.xaxis.pane.set_edgecolor(c["border"])
        ax.yaxis.pane.set_edgecolor(c["border"])
        ax.zaxis.pane.set_edgecolor(c["border"])
        ax.grid(color=c["border"], linewidth=0.4)

        self._vol_fig.tight_layout()
        self._vol_canvas.draw()

    # ── Strategy builder (embedded, with leg editor + chart) ──
    def _build_strategy_chart(self, parent):
        c = self._c

        # ── Preset definitions ──
        # Each entry: list of (opt_type, side, strike_multiplier, qty)
        self._strategy_presets = {
            "Long Call":             [("call", "long",  1.00, 1)],
            "Long Put":              [("put",  "long",  1.00, 1)],
            "Short Call":            [("call", "short", 1.00, 1)],
            "Short Put":             [("put",  "short", 1.00, 1)],
            "Bull Call Spread":      [("call", "long",  0.95, 1),
                                      ("call", "short", 1.05, 1)],
            "Bear Put Spread":       [("put",  "long",  1.05, 1),
                                      ("put",  "short", 0.95, 1)],
            "Long Straddle":         [("call", "long",  1.00, 1),
                                      ("put",  "long",  1.00, 1)],
            "Short Straddle":        [("call", "short", 1.00, 1),
                                      ("put",  "short", 1.00, 1)],
            "Long Strangle":         [("call", "long",  1.05, 1),
                                      ("put",  "long",  0.95, 1)],
            "Short Strangle":        [("call", "short", 1.05, 1),
                                      ("put",  "short", 0.95, 1)],
            "Iron Condor":           [("put",  "long",  0.85, 1),
                                      ("put",  "short", 0.95, 1),
                                      ("call", "short", 1.05, 1),
                                      ("call", "long",  1.15, 1)],
            "Iron Butterfly":        [("put",  "long",  0.95, 1),
                                      ("put",  "short", 1.00, 1),
                                      ("call", "short", 1.00, 1),
                                      ("call", "long",  1.05, 1)],
            "Butterfly (Calls)":     [("call", "long",  0.95, 1),
                                      ("call", "short", 1.00, 2),
                                      ("call", "long",  1.05, 1)],
            "Short Butterfly (Calls)":[("call","short", 0.95, 1),
                                      ("call", "long",  1.00, 2),
                                      ("call", "short", 1.05, 1)],
            "Short Condor (Calls)":  [("call", "short", 0.93, 1),
                                      ("call", "long",  0.97, 1),
                                      ("call", "long",  1.03, 1),
                                      ("call", "short", 1.07, 1)],
            "Covered Call":          [("call", "short", 1.05, 1)],  # +stock added in plot
            "Protective Put":        [("put",  "long",  0.95, 1)],  # +stock added in plot
        }

        # ── Split layout: top = controls+table+editor, bottom = chart ──
        # Use a PanedWindow so the user can resize
        pane = tk.PanedWindow(parent, orient="vertical",
                              bg=c["bg"], sashwidth=5, sashrelief="flat")
        pane.pack(fill="both", expand=True)

        top_pane = tk.Frame(pane, bg=c["bg"])
        bot_pane = tk.Frame(pane, bg=c["bg"])
        pane.add(top_pane, minsize=220)
        pane.add(bot_pane, minsize=160)

        # ── ROW 0: Preset selector ──
        preset_row = tk.Frame(top_pane, bg=c["bg"])
        preset_row.pack(fill="x", padx=6, pady=(6, 2))

        tk.Label(preset_row, text="Preset:", bg=c["bg"], fg=c["fg"],
                 font=("Courier New", 9)).pack(side="left")

        self.strategy_preset_var = tk.StringVar(value="Long Straddle")
        preset_combo = ttk.Combobox(preset_row,
                                    textvariable=self.strategy_preset_var,
                                    values=list(self._strategy_presets.keys()),
                                    width=22, state="readonly",
                                    font=("Courier New", 9))
        preset_combo.pack(side="left", padx=(6, 8))

        ttk.Button(preset_row, text="Load Preset",
                   command=self._load_preset_legs).pack(side="left", padx=(0, 16))

        self.strat_show_today = tk.BooleanVar(value=True)
        ttk.Checkbutton(preset_row, text="Show today (BS)",
                        variable=self.strat_show_today).pack(side="left")

        # ── ROW 1: Legs Treeview ──
        tree_frame = tk.Frame(top_pane, bg=c["bg"])
        tree_frame.pack(fill="x", padx=6, pady=(2, 0))

        cols = ("qty", "position", "type", "strike", "premium", "mode")
        self._legs_tree = ttk.Treeview(tree_frame, columns=cols,
                                       show="headings", height=5)
        hdrs = {"qty": ("Qty", 42), "position": ("Position", 72),
                "type": ("Type", 60), "strike": ("Strike", 80),
                "premium": ("Premium", 80), "mode": ("Mode", 60)}
        for col, (hdr, w) in hdrs.items():
            self._legs_tree.heading(col, text=hdr)
            self._legs_tree.column(col, width=w, anchor="center")

        # Style the treeview to match dark theme
        style = ttk.Style()
        style.configure("Treeview",
                        background=c["panel"], foreground=c["fg"],
                        fieldbackground=c["panel"], rowheight=20,
                        font=("Courier New", 9))
        style.configure("Treeview.Heading",
                        background=c["btn"], foreground=c["acc"],
                        font=("Courier New", 9, "bold"))
        style.map("Treeview", background=[("selected", c["acc"])],
                  foreground=[("selected", "#ffffff")])

        sb = ttk.Scrollbar(tree_frame, orient="vertical",
                           command=self._legs_tree.yview)
        self._legs_tree.configure(yscrollcommand=sb.set)
        self._legs_tree.pack(side="left", fill="x", expand=True)
        sb.pack(side="right", fill="y")

        self._legs_tree.bind("<<TreeviewSelect>>",
                             lambda _: self._populate_leg_editor_from_selection())

        # ── ROW 2: Leg editor ──
        editor = ttk.LabelFrame(top_pane, text="EDIT / ADD LEG", padding=8)
        editor.pack(fill="x", padx=6, pady=(4, 2))

        # Editor vars
        self._leg_qty_var      = tk.StringVar(value="1")
        self._leg_pos_var      = tk.StringVar(value="long")
        self._leg_type_var     = tk.StringVar(value="call")
        self._leg_strike_var   = tk.StringVar(value="")
        self._leg_use_prem_var = tk.BooleanVar(value=False)
        self._leg_prem_var     = tk.StringVar(value="")

        # Row A: qty / position / type / strike
        row_a = tk.Frame(editor, bg=c["bg"])
        row_a.pack(fill="x", pady=(0, 4))

        for lbl, var, opts, w in [
            ("Qty",      self._leg_qty_var,  None,              5),
            ("Position", self._leg_pos_var,  ["long","short"],  8),
            ("Type",     self._leg_type_var, ["call","put"],    8),
            ("Strike",   self._leg_strike_var, None,           10),
        ]:
            tk.Label(row_a, text=lbl, bg=c["bg"], fg=c["fg"],
                     font=("Courier New", 9)).pack(side="left", padx=(6, 2))
            if opts:
                ttk.Combobox(row_a, textvariable=var, values=opts,
                             state="readonly", width=w,
                             font=("Courier New", 9)).pack(side="left")
            else:
                tk.Entry(row_a, textvariable=var, width=w,
                         bg=c["panel"], fg=c["acc2"],
                         insertbackground=c["fg"], relief="flat",
                         font=("Courier New", 9)).pack(side="left")

        # Row B: custom premium toggle
        row_b = tk.Frame(editor, bg=c["bg"])
        row_b.pack(fill="x", pady=(0, 4))

        ttk.Checkbutton(row_b, text="Custom premium",
                        variable=self._leg_use_prem_var,
                        command=self._toggle_prem_entry).pack(side="left")
        tk.Label(row_b, text="Value:", bg=c["bg"], fg=c["fg"],
                 font=("Courier New", 9)).pack(side="left", padx=(12, 4))
        self._leg_prem_entry = tk.Entry(
            row_b, textvariable=self._leg_prem_var, width=10,
            bg=c["panel"], fg=c["acc2"], insertbackground=c["fg"],
            relief="flat", font=("Courier New", 9), state="disabled")
        self._leg_prem_entry.pack(side="left")
        tk.Label(row_b, text="  (leave blank = BS price)",
                 bg=c["bg"], fg="#555577",
                 font=("Courier New", 8)).pack(side="left", padx=6)

        # Row C: action buttons
        row_c = tk.Frame(editor, bg=c["bg"])
        row_c.pack(fill="x")

        ttk.Button(row_c, text="+ Add Leg",
                   command=self._add_leg_from_editor).pack(side="left", padx=(0, 6))
        ttk.Button(row_c, text="✎ Update",
                   command=self._update_selected_leg).pack(side="left", padx=(0, 6))
        ttk.Button(row_c, text="✕ Delete",
                   command=self._delete_selected_leg).pack(side="left", padx=(0, 16))
        ttk.Button(row_c, text="🗑 Clear All",
                   command=self._clear_legs_tree).pack(side="left", padx=(0, 16))
        ttk.Button(row_c, text="▶  PLOT STRATEGY",
                   command=self._refresh_strategy,
                   style="Accent.TButton").pack(side="right")

        # ── Chart (bottom pane) ──
        chart_container = tk.Frame(bot_pane, bg=c["bg"])
        chart_container.pack(fill="both", expand=True, padx=4, pady=4)
        
        self._strat_fig, self._strat_ax = plt.subplots(figsize=(6, 3.2))
        self._strat_fig.patch.set_facecolor(c["bg"])
        self._strat_ax.set_facecolor(c["panel"])
        self._strat_canvas = FigureCanvasTkAgg(self._strat_fig, master=chart_container)
        
        # Add matplotlib navigation toolbar
        from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk
        toolbar = NavigationToolbar2Tk(self._strat_canvas, chart_container)
        toolbar.update()
        # Style toolbar
        toolbar.config(bg=c["panel"])
        for child in toolbar.winfo_children():
            try:
                child.config(bg=c["panel"], fg=c["fg"])
            except:
                pass
        
        self._strat_canvas.get_tk_widget().pack(fill="both", expand=True)

        # Tooltip state
        self._strat_tooltip_ann = None
        self._strat_motion_cid  = None
        self._strat_S_vals      = None
        self._strat_pnl_vals    = None

        # Load default preset and draw
        self._load_preset_legs()

    # ── Leg editor helpers ──────────────────────────────────────────────────

    def _toggle_prem_entry(self):
        if self._leg_use_prem_var.get():
            self._leg_prem_entry.config(state="normal")
        else:
            self._leg_prem_entry.config(state="disabled")
            self._leg_prem_var.set("")

    def _legs_tree_insert(self, qty, position, opt_type, strike,
                          premium_override=None):
        """Insert one leg row into the treeview."""
        if premium_override is None:
            prem_txt, mode = "", "BS"
        else:
            prem_txt = f"{float(premium_override):.4f}"
            mode = "CUSTOM"
        self._legs_tree.insert(
            "", "end",
            values=(qty, position, opt_type, f"{float(strike):.2f}",
                    prem_txt, mode))

    def _read_leg_editor(self):
        """Validate and return (qty, position, opt_type, strike, premium_override)."""
        try:
            qty = int(self._leg_qty_var.get())
            if qty == 0:
                raise ValueError
        except Exception:
            raise ValueError("Qty must be a non-zero integer.")

        position = self._leg_pos_var.get().strip().lower()
        if position not in ("long", "short"):
            raise ValueError("Position must be long or short.")

        opt_type = self._leg_type_var.get().strip().lower()
        if opt_type not in ("call", "put"):
            raise ValueError("Type must be call or put.")

        try:
            strike = float(self._leg_strike_var.get())
            if strike <= 0:
                raise ValueError
        except Exception:
            raise ValueError("Strike must be a positive number.")

        premium_override = None
        if self._leg_use_prem_var.get():
            try:
                premium_override = float(self._leg_prem_var.get())
                if premium_override < 0:
                    raise ValueError
            except Exception:
                raise ValueError("Custom premium must be ≥ 0.")

        return qty, position, opt_type, strike, premium_override

    def _add_leg_from_editor(self):
        try:
            qty, pos, ot, strike, prem = self._read_leg_editor()
            self._legs_tree_insert(qty, pos, ot, strike, prem)
        except ValueError as e:
            messagebox.showerror("Leg Input Error", str(e))

    def _update_selected_leg(self):
        sel = self._legs_tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Click a leg row first.")
            return
        try:
            qty, pos, ot, strike, prem = self._read_leg_editor()
            prem_txt = "" if prem is None else f"{float(prem):.4f}"
            mode     = "BS" if prem is None else "CUSTOM"
            self._legs_tree.item(
                sel[0],
                values=(qty, pos, ot, f"{strike:.2f}", prem_txt, mode))
        except ValueError as e:
            messagebox.showerror("Leg Update Error", str(e))

    def _delete_selected_leg(self):
        sel = self._legs_tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Click a leg row first.")
            return
        self._legs_tree.delete(sel[0])

    def _clear_legs_tree(self):
        for item in self._legs_tree.get_children():
            self._legs_tree.delete(item)

    def _populate_leg_editor_from_selection(self):
        sel = self._legs_tree.selection()
        if not sel:
            return
        qty, pos, ot, strike, prem, mode = self._legs_tree.item(sel[0], "values")
        self._leg_qty_var   .set(str(qty))
        self._leg_pos_var   .set(pos)
        self._leg_type_var  .set(ot)
        self._leg_strike_var.set(str(strike))
        if prem and str(prem).strip():
            self._leg_use_prem_var.set(True)
            self._leg_prem_var.set(str(prem))
        else:
            self._leg_use_prem_var.set(False)
            self._leg_prem_var.set("")
        self._toggle_prem_entry()

    def _get_legs_from_tree(self):
        """Return list of leg dicts from treeview."""
        legs = []
        for item in self._legs_tree.get_children():
            qty, pos, ot, strike, prem, mode = self._legs_tree.item(item, "values")
            prem_val = float(prem) if prem and str(prem).strip() else None
            legs.append({"qty": int(qty), "position": pos, "type": ot,
                         "strike": float(strike), "premium_override": prem_val})
        return legs

    def _load_preset_legs(self):
        """Populate the treeview from the selected preset."""
        try:
            K = float(self.strike_price.get())
        except Exception:
            K = 100.0

        preset_name = self.strategy_preset_var.get()
        template    = self._strategy_presets.get(preset_name, [])

        self._clear_legs_tree()
        for opt_type, side, k_mult, qty in template:
            self._legs_tree_insert(qty, side, opt_type, K * k_mult)

        # Pre-fill strike editor with base K
        self._leg_strike_var.set(f"{K:.2f}")

    # ── Strategy plot ───────────────────────────────────────────────────────

    def _refresh_strategy(self):
        c  = self._c
        ax = self._strat_ax
        ax.clear()

        # Pull global parameters
        try:
            S     = float(self.current_price.get())
            T_day = float(self.time_to_expiry.get())
            r     = float(self.risk_free_rate.get())
            sig   = float(self.volatility.get())
        except (ValueError, tk.TclError):
            ax.set_title("Enter parameters and click ▶ PLOT STRATEGY",
                         color=c["fg"], fontsize=9)
            self._strat_canvas.draw()
            return

        T = max(T_day / 365.0, 1e-6)

        legs = self._get_legs_from_tree()
        if not legs:
            ax.set_title("No legs defined – load a preset or add legs manually",
                         color=c["fg"], fontsize=9)
            self._strat_canvas.draw()
            return

        # Check if any BS-priced leg needs valid vol
        needs_bs = any(lg["premium_override"] is None for lg in legs)
        if needs_bs and sig <= 0:
            messagebox.showerror("Input Error",
                                 "Volatility must be > 0 for BS-priced legs.")
            return

        S_range = np.linspace(S * 0.60, S * 1.40, 400)
        combined_expiry = np.zeros(len(S_range))
        combined_today  = np.zeros(len(S_range))
        total_premium   = 0.0
        leg_descs        = []

        for lg in legs:
            qty      = lg["qty"]
            opt_type = lg["type"]
            position = lg["position"]
            leg_K    = lg["strike"]
            sign     = 1 if position == "long" else -1

            # Premium: custom or Black-Scholes
            if lg["premium_override"] is not None:
                leg_price = lg["premium_override"]
                src = "CUST"
            else:
                try:
                    leg_price, *_ = self._black_scholes_price(
                        S, leg_K, T, r, sig, opt_type)
                except ValueError:
                    leg_price = 0.0
                src = "BS"

            # Expiry payoff
            if opt_type == "call":
                expiry_payoff = np.maximum(S_range - leg_K, 0)
            else:
                expiry_payoff = np.maximum(leg_K - S_range, 0)

            combined_expiry += sign * qty * (expiry_payoff - leg_price)
            total_premium   += sign * qty * leg_price

            # Today's BS theoretical value (always from BS regardless of prem mode)
            try:
                today_vals = np.array([
                    self._black_scholes_price(s, leg_K, T, r, sig, opt_type)[0]
                    for s in S_range
                ])
            except ValueError:
                today_vals = np.zeros(len(S_range))
            combined_today += sign * qty * (today_vals - leg_price)

            action = "BUY" if position == "long" else "SELL"
            leg_descs.append(
                f"{action} {qty}× {opt_type.upper()} K={leg_K:.2f} "
                f"@ ${leg_price:.2f} [{src}]")

        # Stock legs for covered call / protective put
        preset_name = self.strategy_preset_var.get()
        if preset_name in ("Covered Call", "Protective Put"):
            stock_pnl = S_range - S
            combined_expiry += stock_pnl
            combined_today  += stock_pnl
            leg_descs.append(f"LONG 1× STOCK @ ${S:.2f}")

        # ── Draw ──
        ax.axhline(0, color=c["border"], linewidth=0.8, linestyle="--")
        ax.axvline(S, color="#555577", linewidth=1, linestyle=":",
                   label=f"Spot {S:.1f}")

        # Strike verticals
        seen_k: set = set()
        for lg in legs:
            lk = round(lg["strike"], 2)
            if lk not in seen_k:
                ax.axvline(lk, color=c["acc"], linewidth=0.8,
                           linestyle="--", alpha=0.5)
                seen_k.add(lk)

        # P&L fills
        ax.fill_between(S_range, combined_expiry, 0,
                        where=(combined_expiry >= 0),
                        alpha=0.13, color="#4ecdc4", zorder=1)
        ax.fill_between(S_range, combined_expiry, 0,
                        where=(combined_expiry < 0),
                        alpha=0.13, color="#cc4444", zorder=1)

        ax.plot(S_range, combined_expiry, color=c["acc2"], linewidth=2.2,
                label="At expiry", zorder=3)

        if self.strat_show_today.get():
            ax.plot(S_range, combined_today, color=c["acc"], linewidth=1.5,
                    linestyle="--", alpha=0.85, label="Today (BS)", zorder=2)

        # Break-even markers
        sign_changes = np.where(np.diff(np.sign(combined_expiry)))[0]
        for idx in sign_changes:
            x_be = S_range[idx]
            ax.axvline(x_be, color="#ffcc44", linewidth=0.8,
                       linestyle=":", alpha=0.9)
            ax.text(x_be, 0, f"BE\n{x_be:.1f}", color="#ffcc44",
                    fontsize=6.5, ha="center", va="bottom")

        prem_sign = "credit" if total_premium < 0 else "debit"
        n_legs    = len(legs)
        ax.set_title(
            f"{preset_name}  ·  {n_legs} leg{'s' if n_legs!=1 else ''}  ·  "
            f"Net {prem_sign}: ${abs(total_premium):.2f}  ·  "
            f"T={T_day:.0f}d  σ={sig:.1%}",
            color=c["fg"], fontsize=8.5, pad=6)
        ax.set_xlabel("Spot at Expiry", color=c["fg"], fontsize=8)
        ax.set_ylabel("P&L",           color=c["fg"], fontsize=8)
        ax.tick_params(colors=c["fg"], labelsize=7)
        for spine in ax.spines.values():
            spine.set_edgecolor(c["border"])
        ax.legend(fontsize=7.5, facecolor=c["panel"],
                  labelcolor=c["fg"], edgecolor=c["border"],
                  loc="upper left")

        self._strat_fig.tight_layout(pad=1.2)
        self._strat_canvas.draw()

        # ── Tooltip ──
        self._strat_S_vals   = S_range
        self._strat_pnl_vals = combined_expiry
        self._bind_strat_tooltip()

        self._update_status(
            f"Strategy plotted: {preset_name}  ({n_legs} legs)  "
            f"Net {prem_sign} ${abs(total_premium):.2f}  |  "
            + "   ".join(leg_descs))

    def _bind_strat_tooltip(self):
        """Attach hover tooltip to strategy chart."""
        c = self._c
        if self._strat_tooltip_ann is not None:
            try:
                self._strat_tooltip_ann.remove()
            except Exception:
                pass
        if self._strat_motion_cid is not None:
            try:
                self._strat_canvas.mpl_disconnect(self._strat_motion_cid)
            except Exception:
                pass

        self._strat_tooltip_ann = self._strat_ax.annotate(
            "", xy=(0, 0), xytext=(12, 12), textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.3", fc="#ffee88", alpha=0.85),
            arrowprops=dict(arrowstyle="->"), fontsize=8)
        self._strat_tooltip_ann.set_visible(False)

        def _on_move(event):
            if event.inaxes != self._strat_ax:
                self._strat_tooltip_ann.set_visible(False)
                self._strat_canvas.draw_idle()
                return
            if self._strat_S_vals is None or event.xdata is None:
                return
            idx = np.abs(self._strat_S_vals - event.xdata).argmin()
            x, y = self._strat_S_vals[idx], self._strat_pnl_vals[idx]
            self._strat_tooltip_ann.xy = (x, y)
            self._strat_tooltip_ann.set_text(f"S = {x:.2f}\nP&L = {y:.2f}")
            self._strat_tooltip_ann.set_visible(True)
            self._strat_canvas.draw_idle()

        self._strat_motion_cid = self._strat_canvas.mpl_connect(
            "motion_notify_event", _on_move)

    # ──────────────────────── OTC BOOK TAB ───────────────────────────────────

    # Parametric vol-surface shape — identical to the VOL SURFACE chart
    _SURF_SKEW  = -0.15   # skew slope  per unit moneyness
    _SURF_SMILE =  0.10   # smile curvature
    _SURF_TERM  =  0.05   # term-structure level
    _SURF_DECAY = 180.0   # term-structure half-life (days)

    @staticmethod
    def _vol_from_surface(strike: float, spot: float,
                          T_days: float, atm_vol: float) -> float:
        """
        Read implied vol off the parametric smile surface for one option.
        Uses the same shape as the VOL SURFACE chart.
          IV = atm_vol + skew*(m-1) + smile*(m-1)² + term_st
        where  m = K/S,  term_st = TERM*(1 − exp(−T/DECAY))
        """
        m        = strike / spot
        skew     = OptionPricingCalculator._SURF_SKEW  * (m - 1.0)
        smile    = OptionPricingCalculator._SURF_SMILE * (m - 1.0) ** 2
        term_st  = OptionPricingCalculator._SURF_TERM  * (
            1.0 - np.exp(-T_days / OptionPricingCalculator._SURF_DECAY))
        return float(np.clip(atm_vol + skew + smile + term_st, 0.01, 1.50))

    def _otc_resolve_vol(self, pos: dict, S: float,
                         T_days: float, global_vol: float) -> tuple:
        """
        Determine the BS vol for MTM and return (vol, updated_atm_vol).

        Rules
        ─────
        • vol_override set on position → use it directly; surface unchanged.
        • No prior surface_atm_vol  → initialise anchor from global_vol.
        • |global_vol − stored_atm| > 0.1%  → user recalibrated the surface;
          update the anchor and interpolate smile at new spot.
        • Otherwise (only spot moved) → keep anchor, slide along the smile.
        """
        if pos.get("vol_override"):
            return float(pos["vol_override"]), pos.get("surface_atm_vol", global_vol)

        stored_atm = pos.get("surface_atm_vol")
        if stored_atm is None or abs(global_vol - stored_atm) > 0.001:
            new_atm = global_vol          # surface recalibrated (or first MTM)
        else:
            new_atm = stored_atm          # spot moved only — anchor unchanged

        vol = self._vol_from_surface(pos["strike"], S, T_days, new_atm)
        return vol, new_atm

    @staticmethod
    def _otc_cum_pnl(pos: dict, spot: float, bs_price: float) -> float:
        """
        Long  → full BS MTM P&L  : (bs_price − trade_premium) × notional
        Short → premium-based P&L: (trade_premium − intrinsic)  × notional
                Max profit = premium received; loss grows only with intrinsic value.
                intrinsic = max(S−K, 0) for calls, max(K−S, 0) for puts.
        """
        notional = pos["notional"]
        trade_px = pos["trade_premium"]
        if pos["position"] == "long":
            return (bs_price - trade_px) * notional
        strike   = pos["strike"]
        intrinsic = (max(spot - strike, 0) if pos["opt_type"] == "call"
                     else max(strike - spot, 0))
        return (trade_px - intrinsic) * notional

    def _build_otc_book_tab(self, parent):
        c = self._c

        pane = tk.PanedWindow(parent, orient="vertical",
                              bg=c["bg"], sashwidth=5, sashrelief="flat")
        pane.pack(fill="both", expand=True)

        top_pane = tk.Frame(pane, bg=c["bg"])
        bot_pane = tk.Frame(pane, bg=c["bg"])
        pane.add(top_pane, minsize=200)
        pane.add(bot_pane, minsize=200)

        # ── Controls row ──
        ctrl = tk.Frame(top_pane, bg=c["bg"])
        ctrl.pack(fill="x", padx=6, pady=(6, 2))

        ttk.Button(ctrl, text="+ Add",
                   command=self._otc_add_new).pack(side="left", padx=(0, 4))
        ttk.Button(ctrl, text="✎ Edit",
                   command=self._otc_edit_selected).pack(side="left", padx=(0, 4))
        ttk.Button(ctrl, text="✕ Delete",
                   command=self._otc_delete_selected).pack(side="left", padx=(0, 10))

        tk.Label(ctrl, text="|", bg=c["bg"], fg="#444466",
                 font=("Courier New", 9)).pack(side="left", padx=4)

        ttk.Button(ctrl, text="▶ MTM All",
                   command=self._otc_run_mtm,
                   style="Accent.TButton").pack(side="left", padx=(0, 4))
        ttk.Button(ctrl, text="▶ MTM Selected",
                   command=self._otc_run_mtm_selected).pack(side="left", padx=(0, 4))
        ttk.Button(ctrl, text="📜 History",
                   command=self._otc_show_history).pack(side="left", padx=(0, 10))

        tk.Label(ctrl, text="|", bg=c["bg"], fg="#444466",
                 font=("Courier New", 9)).pack(side="left", padx=4)

        ttk.Button(ctrl, text="💾 Save",
                   command=self._otc_save_book).pack(side="left", padx=(0, 4))
        ttk.Button(ctrl, text="📂 Load",
                   command=self._otc_load_book).pack(side="left")

        # MTM note
        tk.Label(ctrl,
                 text="  ← Spot from Parameters (or override). "
                      "Vol auto-read from smile surface; change global Vol to recalibrate.",
                 bg=c["bg"], fg="#445566",
                 font=("Courier New", 8)).pack(side="left", padx=8)

        # ── Positions treeview ──
        tree_frame = tk.Frame(top_pane, bg=c["bg"])
        tree_frame.pack(fill="both", expand=True, padx=6, pady=(2, 4))

        cols = ("cpty", "und", "type", "pos", "strike", "expiry",
                "dte", "notional", "trade_px", "mtm_px", "pnl", "updated")
        self._otc_tree = ttk.Treeview(tree_frame, columns=cols,
                                      show="headings", height=8)
        headers = {
            "cpty":    ("Counterparty", 90),
            "und":     ("Underlying",   72),
            "type":    ("Type",         46),
            "pos":     ("Pos",          46),
            "strike":  ("Strike",       68),
            "expiry":  ("Expiry",       82),
            "dte":     ("DTE",          38),
            "notional":("Notional",     72),
            "trade_px":("Trade Px",     70),
            "mtm_px":  ("MTM Px",       70),
            "pnl":     ("Cum P&L",      82),
            "updated": ("Last MTM",     82),
        }
        for col, (hdr, w) in headers.items():
            self._otc_tree.heading(col, text=hdr)
            self._otc_tree.column(col, width=w, anchor="center", minwidth=w)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",
                            command=self._otc_tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal",
                            command=self._otc_tree.xview)
        self._otc_tree.configure(yscrollcommand=vsb.set,
                                 xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self._otc_tree.pack(fill="both", expand=True)

        self._otc_tree.tag_configure("profit", foreground="#4ecdc4")
        self._otc_tree.tag_configure("loss",   foreground="#cc4444")
        self._otc_tree.tag_configure("flat",   foreground=c["fg"])

        # ── Entry / Edit form ──
        form = ttk.LabelFrame(bot_pane, text="ADD / EDIT POSITION", padding=8)
        form.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        self._otc_cpty_var      = tk.StringVar()
        self._otc_und_var       = tk.StringVar()
        self._otc_type_var      = tk.StringVar(value="call")
        self._otc_pos_var       = tk.StringVar(value="long")
        self._otc_strike_var    = tk.StringVar()
        self._otc_expiry_var    = tk.StringVar()
        self._otc_notl_var      = tk.StringVar(value="1000")
        self._otc_trade_px_var  = tk.StringVar()
        self._otc_rate_var      = tk.StringVar(value="0.05")
        self._otc_trade_date_var= tk.StringVar(
            value=datetime.now().strftime("%Y-%m-%d"))
        self._otc_spot_ovr_var  = tk.StringVar()
        self._otc_vol_ovr_var   = tk.StringVar()

        # Row 1: Counterparty / Underlying / Type / Position
        row1 = tk.Frame(form, bg=c["bg"])
        row1.pack(fill="x", pady=(0, 4))
        for lbl, var, opts, w in [
            ("Counterparty", self._otc_cpty_var,  None,             11),
            ("Underlying",   self._otc_und_var,   None,              8),
            ("Type",         self._otc_type_var,  ["call","put"],    7),
            ("Position",     self._otc_pos_var,   ["long","short"],  7),
        ]:
            tk.Label(row1, text=lbl, bg=c["bg"], fg=c["fg"],
                     font=("Courier New", 9)).pack(side="left", padx=(4, 2))
            if opts:
                ttk.Combobox(row1, textvariable=var, values=opts,
                             state="readonly", width=w,
                             font=("Courier New", 9)).pack(side="left")
            else:
                tk.Entry(row1, textvariable=var, width=w,
                         bg=c["panel"], fg=c["acc2"],
                         insertbackground=c["fg"], relief="flat",
                         font=("Courier New", 9)).pack(side="left")

        # Row 2: Strike / Expiry / Notional / Trade Px / Rate
        row2 = tk.Frame(form, bg=c["bg"])
        row2.pack(fill="x", pady=(0, 4))
        for lbl, var, w, hint in [
            ("Strike",    self._otc_strike_var,   8,  ""),
            ("Expiry",    self._otc_expiry_var,   10, "YYYY-MM-DD"),
            ("Notional",  self._otc_notl_var,      8,  "units"),
            ("Trade Px",  self._otc_trade_px_var,  8,  "per unit"),
            ("Rate",      self._otc_rate_var,       6,  ""),
        ]:
            tk.Label(row2, text=lbl, bg=c["bg"], fg=c["fg"],
                     font=("Courier New", 9)).pack(side="left", padx=(4, 2))
            e = tk.Entry(row2, textvariable=var, width=w,
                         bg=c["panel"], fg=c["acc2"],
                         insertbackground=c["fg"], relief="flat",
                         font=("Courier New", 9))
            e.pack(side="left")
            if hint:
                tk.Label(row2, text=hint, bg=c["bg"], fg="#445566",
                         font=("Courier New", 7)).pack(side="left", padx=(1, 4))

        # Row 3: Trade Date / Spot Override / Vol Override
        row3 = tk.Frame(form, bg=c["bg"])
        row3.pack(fill="x", pady=(0, 4))
        for lbl, var, w, hint in [
            ("Trade Date",    self._otc_trade_date_var, 10, "YYYY-MM-DD"),
            ("Spot Override", self._otc_spot_ovr_var,   8,  "blank=global"),
            ("Vol Override",  self._otc_vol_ovr_var,    8,  "blank=global"),
        ]:
            tk.Label(row3, text=lbl, bg=c["bg"], fg=c["fg"],
                     font=("Courier New", 9)).pack(side="left", padx=(4, 2))
            e = tk.Entry(row3, textvariable=var, width=w,
                         bg=c["panel"], fg=c["acc2"],
                         insertbackground=c["fg"], relief="flat",
                         font=("Courier New", 9))
            e.pack(side="left")
            tk.Label(row3, text=hint, bg=c["bg"], fg="#445566",
                     font=("Courier New", 7)).pack(side="left", padx=(1, 6))

        # Row 4: action buttons
        row4 = tk.Frame(form, bg=c["bg"])
        row4.pack(fill="x", pady=(2, 0))
        ttk.Button(row4, text="✕ Clear",
                   command=self._otc_clear_form).pack(side="left", padx=(0, 6))
        self._otc_save_btn = ttk.Button(
            row4, text="✓ Save Position",
            command=self._otc_save_position,
            style="Accent.TButton")
        self._otc_save_btn.pack(side="right")

        # Load persisted data
        self._load_otc_book()

    # ── OTC helpers ──────────────────────────────────────────────────────────

    def _otc_refresh_tree(self):
        for item in self._otc_tree.get_children():
            self._otc_tree.delete(item)
        today = datetime.now().date()
        for pos in self.otc_positions:
            try:
                exp = datetime.strptime(pos["expiry_date"], "%Y-%m-%d").date()
                dte = max((exp - today).days, 0)
            except Exception:
                dte = "?"
            if pos["mtm_history"]:
                last    = pos["mtm_history"][-1]
                mtm_px  = f"{last['bs_price']:.4f}"
                cum_pnl = self._otc_cum_pnl(pos, last["spot"], last["bs_price"])
                pnl_str = f"{cum_pnl:+,.2f}"
                updated = last["date"]
                tag = "profit" if cum_pnl > 0 else ("loss" if cum_pnl < 0 else "flat")
            else:
                mtm_px  = "-"
                pnl_str = "-"
                updated = "-"
                tag = "flat"
            self._otc_tree.insert(
                "", "end", iid=pos["id"],
                values=(
                    pos["counterparty"],
                    pos["underlying"],
                    pos["opt_type"].upper(),
                    pos["position"].upper(),
                    f"{pos['strike']:.2f}",
                    pos["expiry_date"],
                    str(dte),
                    f"{pos['notional']:,.0f}",
                    f"{pos['trade_premium']:.4f}",
                    mtm_px,
                    pnl_str,
                    updated,
                ),
                tags=(tag,),
            )

    def _otc_clear_form(self):
        self._otc_editing_id = None
        self._otc_save_btn.config(text="✓ Save Position")
        for var in (self._otc_cpty_var, self._otc_und_var,
                    self._otc_strike_var, self._otc_expiry_var,
                    self._otc_trade_px_var, self._otc_spot_ovr_var,
                    self._otc_vol_ovr_var):
            var.set("")
        self._otc_notl_var.set("1000")
        self._otc_rate_var.set("0.05")
        self._otc_trade_date_var.set(datetime.now().strftime("%Y-%m-%d"))
        self._otc_type_var.set("call")
        self._otc_pos_var.set("long")

    def _otc_add_new(self):
        self._otc_clear_form()

    def _otc_edit_selected(self):
        sel = self._otc_tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Select a position to edit.")
            return
        pos = next((p for p in self.otc_positions if p["id"] == sel[0]), None)
        if not pos:
            return
        self._otc_editing_id = pos["id"]
        self._otc_save_btn.config(text="✓ Update Position")
        self._otc_cpty_var.set(pos["counterparty"])
        self._otc_und_var.set(pos["underlying"])
        self._otc_type_var.set(pos["opt_type"])
        self._otc_pos_var.set(pos["position"])
        self._otc_strike_var.set(f"{pos['strike']:.2f}")
        self._otc_expiry_var.set(pos["expiry_date"])
        self._otc_notl_var.set(f"{pos['notional']:.0f}")
        self._otc_trade_px_var.set(f"{pos['trade_premium']:.4f}")
        self._otc_rate_var.set(f"{pos.get('rate', 0.05):.4f}")
        self._otc_trade_date_var.set(pos.get("trade_date", ""))
        self._otc_spot_ovr_var.set(
            f"{pos['spot_override']:.4f}" if pos.get("spot_override") else "")
        self._otc_vol_ovr_var.set(
            f"{pos['vol_override']:.4f}" if pos.get("vol_override") else "")

    def _otc_delete_selected(self):
        sel = self._otc_tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Select a position to delete.")
            return
        pos = next((p for p in self.otc_positions if p["id"] == sel[0]), None)
        if not pos:
            return
        label = (f"{pos['counterparty']}  {pos['underlying']}  "
                 f"{pos['opt_type'].upper()}  K={pos['strike']:.2f}")
        if messagebox.askyesno("Confirm Delete", f"Delete position:\n{label}?"):
            self.otc_positions = [p for p in self.otc_positions
                                  if p["id"] != sel[0]]
            self._otc_refresh_tree()
            self._otc_save_book_silent()

    def _otc_save_position(self):
        try:
            cpty       = self._otc_cpty_var.get().strip()
            und        = self._otc_und_var.get().strip().upper()
            opt_type   = self._otc_type_var.get()
            position   = self._otc_pos_var.get()
            strike     = float(self._otc_strike_var.get())
            expiry_str = self._otc_expiry_var.get().strip()
            notional   = float(self._otc_notl_var.get())
            trade_px   = float(self._otc_trade_px_var.get())
            rate       = float(self._otc_rate_var.get())
            trade_date = self._otc_trade_date_var.get().strip()
            if not cpty:
                raise ValueError("Counterparty is required.")
            if not und:
                raise ValueError("Underlying is required.")
            datetime.strptime(expiry_str, "%Y-%m-%d")
            datetime.strptime(trade_date, "%Y-%m-%d")
            if strike <= 0:
                raise ValueError("Strike must be positive.")
            if notional <= 0:
                raise ValueError("Notional must be positive.")
            if trade_px < 0:
                raise ValueError("Trade premium must be ≥ 0.")
            spot_ovr_str = self._otc_spot_ovr_var.get().strip()
            vol_ovr_str  = self._otc_vol_ovr_var.get().strip()
            spot_ovr = float(spot_ovr_str) if spot_ovr_str else None
            vol_ovr  = float(vol_ovr_str)  if vol_ovr_str  else None
        except ValueError as exc:
            messagebox.showerror("Form Error", str(exc))
            return

        fields = dict(
            counterparty=cpty, underlying=und,
            opt_type=opt_type, position=position,
            strike=strike, expiry_date=expiry_str,
            notional=notional, trade_premium=trade_px,
            rate=rate, trade_date=trade_date,
            spot_override=spot_ovr, vol_override=vol_ovr,
        )

        if self._otc_editing_id:
            for pos in self.otc_positions:
                if pos["id"] == self._otc_editing_id:
                    # Preserve surface_atm_vol unless vol_override was cleared/changed
                    fields.setdefault("surface_atm_vol", pos.get("surface_atm_vol"))
                    pos.update(fields)
                    break
        else:
            try:
                fields["surface_atm_vol"] = float(self.volatility.get())
            except ValueError:
                fields["surface_atm_vol"] = None
            fields["id"]          = str(uuid.uuid4())[:8]
            fields["mtm_history"] = []
            self.otc_positions.append(fields)

        self._otc_clear_form()
        self._otc_refresh_tree()
        self._otc_save_book_silent()
        self._update_status(
            f"OTC position saved: {cpty}  {und}  "
            f"{opt_type.upper()}  K={strike:.2f}  {position.upper()}")

    # ── MTM engine ───────────────────────────────────────────────────────────

    def _otc_run_mtm(self):
        if not self.otc_positions:
            messagebox.showinfo("OTC MTM", "No positions to mark.")
            return
        try:
            global_spot = float(self.current_price.get())
            global_vol  = float(self.volatility.get())
        except ValueError:
            messagebox.showerror(
                "MTM Error",
                "Set valid Spot and Vol in the Parameters panel first.")
            return

        today_str = datetime.now().strftime("%Y-%m-%d")
        updated, errors = 0, []

        for pos in self.otc_positions:
            try:
                exp    = datetime.strptime(pos["expiry_date"], "%Y-%m-%d")
                T_days = (exp - datetime.now()).days
                if T_days <= 0:
                    errors.append(
                        f"{pos['counterparty']}/{pos['underlying']}: expired")
                    continue
                T   = T_days / 365.0
                r   = pos.get("rate", 0.05)
                S   = pos["spot_override"] if pos.get("spot_override") else global_spot

                vol, new_atm = self._otc_resolve_vol(pos, S, T_days, global_vol)
                pos["surface_atm_vol"] = new_atm

                bs_price, *_ = self._black_scholes_price(
                    S, pos["strike"], T, r, vol, pos["opt_type"])

                cum_pnl   = self._otc_cum_pnl(pos, S, bs_price)
                prev_cum  = (pos["mtm_history"][-1]["cum_pnl"]
                             if pos["mtm_history"] else 0.0)
                daily_pnl = cum_pnl - prev_cum

                entry = dict(date=today_str, spot=S, vol=vol,
                             atm_vol=new_atm, bs_price=bs_price,
                             daily_pnl=daily_pnl, cum_pnl=cum_pnl)

                if (pos["mtm_history"] and
                        pos["mtm_history"][-1]["date"] == today_str):
                    pos["mtm_history"][-1] = entry
                else:
                    pos["mtm_history"].append(entry)
                updated += 1
            except Exception as exc:
                errors.append(
                    f"{pos.get('counterparty','?')}/"
                    f"{pos.get('underlying','?')}: {exc}")

        self._otc_refresh_tree()
        self._otc_save_book_silent()
        msg = f"MTM complete: {updated} position(s) marked for {today_str}."
        if errors:
            msg += "\n\nErrors:\n" + "\n".join(errors)
            messagebox.showwarning("MTM Completed with Errors", msg)
        else:
            messagebox.showinfo("MTM Complete", msg)
        self._update_status(msg.splitlines()[0])

    # ── History popup ─────────────────────────────────────────────────────────

    def _otc_show_history(self):
        sel = self._otc_tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Select a position first.")
            return
        pos = next((p for p in self.otc_positions if p["id"] == sel[0]), None)
        if not pos:
            return

        c = self._c
        popup = tk.Toplevel(self.root)
        popup.title(
            f"MTM History  –  {pos['counterparty']}  "
            f"{pos['underlying']}  {pos['opt_type'].upper()}  K={pos['strike']:.2f}")
        popup.geometry("760x640")
        popup.configure(bg=c["bg"])
        popup.resizable(True, True)

        tk.Label(
            popup,
            text=(f"{pos['counterparty']}  |  {pos['underlying']}  "
                  f"{pos['opt_type'].upper()}  K={pos['strike']:.2f}  |  "
                  f"{pos['position'].upper()}  {pos['notional']:,.0f} units"),
            bg=c["bg"], fg=c["acc"],
            font=("Courier New", 11, "bold")
        ).pack(pady=(10, 2))
        tk.Label(
            popup,
            text=(f"Trade Date: {pos.get('trade_date','-')}   "
                  f"Expiry: {pos['expiry_date']}   "
                  f"Trade Px: {pos['trade_premium']:.4f}   "
                  f"Rate: {pos.get('rate', 0.05):.2%}"),
            bg=c["bg"], fg="#888899",
            font=("Courier New", 9)
        ).pack()

        # ── Main paned area ──
        main_pane = tk.PanedWindow(popup, orient="vertical",
                                   bg=c["bg"], sashwidth=5, sashrelief="flat")
        main_pane.pack(fill="both", expand=True, padx=10, pady=(6, 0))

        tree_pane  = tk.Frame(main_pane, bg=c["bg"])
        edit_pane  = ttk.LabelFrame(main_pane, text="ADD / EDIT MTM ENTRY", padding=8)
        chart_pane = tk.Frame(main_pane, bg=c["bg"])
        main_pane.add(tree_pane,  minsize=120)
        main_pane.add(edit_pane,  minsize=95)
        main_pane.add(chart_pane, minsize=140)

        # ── History treeview ──
        hist_cols = ("date", "spot", "atm_vol", "smile_vol", "bs_price", "daily_pnl", "cum_pnl")
        hist_tree = ttk.Treeview(tree_pane, columns=hist_cols,
                                 show="headings", height=6)
        hist_hdrs = {
            "date":      ("Date",       100),
            "spot":      ("Spot",        76),
            "atm_vol":   ("ATM Vol",     72),
            "smile_vol": ("Smile Vol",   72),
            "bs_price":  ("BS Price",    80),
            "daily_pnl": ("Daily P&L",   88),
            "cum_pnl":   ("Cum P&L",     88),
        }
        for col, (hdr, w) in hist_hdrs.items():
            hist_tree.heading(col, text=hdr)
            hist_tree.column(col, width=w, anchor="center")
        hist_tree.tag_configure("pos", foreground="#4ecdc4")
        hist_tree.tag_configure("neg", foreground="#cc4444")
        vsb = ttk.Scrollbar(tree_pane, orient="vertical",
                            command=hist_tree.yview)
        hist_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        hist_tree.pack(fill="both", expand=True)

        # ── Edit form ──
        try:
            _def_spot = f"{float(self.current_price.get()):.4f}"
            _def_vol  = f"{float(self.volatility.get()):.4f}"
        except Exception:
            _def_spot, _def_vol = "", ""

        entry_date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        entry_spot_var = tk.StringVar(value=_def_spot)
        entry_vol_var  = tk.StringVar(value=_def_vol)

        form_row1 = tk.Frame(edit_pane, bg=c["bg"])
        form_row1.pack(fill="x", pady=(0, 6))
        for lbl, var, w, hint in [
            ("Date",         entry_date_var, 12, "YYYY-MM-DD"),
            ("Spot",         entry_spot_var, 10, ""),
            ("Vol (decimal)",entry_vol_var,  10, "e.g. 0.25"),
        ]:
            tk.Label(form_row1, text=lbl, bg=c["bg"], fg=c["fg"],
                     font=("Courier New", 9)).pack(side="left", padx=(4, 2))
            tk.Entry(form_row1, textvariable=var, width=w,
                     bg=c["panel"], fg=c["acc2"],
                     insertbackground=c["fg"], relief="flat",
                     font=("Courier New", 9)).pack(side="left")
            if hint:
                tk.Label(form_row1, text=hint, bg=c["bg"], fg="#445566",
                         font=("Courier New", 7)).pack(side="left", padx=(2, 8))

        form_row2 = tk.Frame(edit_pane, bg=c["bg"])
        form_row2.pack(fill="x")
        ttk.Button(form_row2, text="✕ Delete Entry",
                   command=lambda: _delete_entry()).pack(side="left", padx=(0, 6))
        ttk.Button(form_row2, text="✓ Update Selected",
                   command=lambda: _update_entry()).pack(side="left", padx=(0, 6))
        ttk.Button(form_row2, text="+ Add Entry",
                   command=lambda: _add_entry(),
                   style="Accent.TButton").pack(side="left")

        # ── Chart ──
        fig, ax = plt.subplots(figsize=(6, 2.2))
        fig.patch.set_facecolor(c["bg"])
        ax.set_facecolor(c["panel"])
        canvas = FigureCanvasTkAgg(fig, master=chart_pane)
        canvas.get_tk_widget().pack(fill="both", expand=True)

        # ── Nested helpers ──────────────────────────────────────────────────

        def _recalc_all():
            pos["mtm_history"].sort(key=lambda x: x["date"])
            for i, h in enumerate(pos["mtm_history"]):
                h["cum_pnl"]   = self._otc_cum_pnl(pos, h["spot"], h["bs_price"])
                h["daily_pnl"] = (0.0 if i == 0
                                  else h["cum_pnl"] - pos["mtm_history"][i - 1]["cum_pnl"])

        def _calc_bs(date_str, spot, vol):
            exp      = datetime.strptime(pos["expiry_date"], "%Y-%m-%d")
            entry_dt = datetime.strptime(date_str, "%Y-%m-%d")
            T_days   = (exp - entry_dt).days
            if T_days <= 0:
                raise ValueError(
                    f"Date {date_str} is at or after expiry {pos['expiry_date']}.")
            bs_price, *_ = self._black_scholes_price(
                spot, pos["strike"], T_days / 365.0,
                pos.get("rate", 0.05), vol, pos["opt_type"])
            return bs_price

        def refresh_tree():
            for item in hist_tree.get_children():
                hist_tree.delete(item)
            for entry in pos["mtm_history"]:
                daily    = entry.get("daily_pnl", 0.0)
                cum      = entry.get("cum_pnl",   0.0)
                atm_v    = entry.get("atm_vol",   entry.get("vol", 0.0))
                smile_v  = entry.get("vol",       0.0)
                hist_tree.insert("", "end", values=(
                    entry["date"],
                    f"{entry['spot']:.2f}",
                    f"{atm_v:.2%}",
                    f"{smile_v:.2%}",
                    f"{entry['bs_price']:.4f}",
                    f"{daily:+,.2f}",
                    f"{cum:+,.2f}",
                ), tags=("pos" if cum >= 0 else "neg",))

        def refresh_chart():
            ax.clear()
            if not pos["mtm_history"]:
                ax.set_title("No MTM history yet", color=c["fg"], fontsize=9)
                canvas.draw()
                return
            dates    = [h["date"]             for h in pos["mtm_history"]]
            cum_pnls = [h.get("cum_pnl", 0.0) for h in pos["mtm_history"]]
            xs = list(range(len(dates)))
            colors = ["#4ecdc4" if p >= 0 else "#cc4444" for p in cum_pnls]
            ax.bar(xs, cum_pnls, color=colors, alpha=0.55)
            ax.plot(xs, cum_pnls, color=c["acc"],
                    linewidth=1.8, marker="o", markersize=4)
            ax.axhline(0, color=c["border"], linewidth=0.8, linestyle="--")
            ax.set_xticks(xs)
            ax.set_xticklabels(dates, rotation=45, fontsize=7)
            ax.set_ylabel("Cum P&L", color=c["fg"], fontsize=8)
            ax.set_title("Cumulative P&L", color=c["fg"], fontsize=9)
            ax.tick_params(colors=c["fg"], labelsize=7)
            for spine in ax.spines.values():
                spine.set_edgecolor(c["border"])
            fig.tight_layout()
            canvas.draw()

        def populate_form(event=None):
            rows = hist_tree.selection()
            if not rows:
                return
            vals = hist_tree.item(rows[0], "values")
            entry_date_var.set(vals[0])   # date
            entry_spot_var.set(vals[1])   # spot
            # vals[3] = Smile Vol (the vol actually used for BS) — pre-fill with that
            try:
                entry_vol_var.set(f"{float(vals[3].rstrip('%')) / 100:.4f}")
            except ValueError:
                entry_vol_var.set(vals[3])

        hist_tree.bind("<<TreeviewSelect>>", populate_form)

        def _commit(date_str, spot, vol, original_date=None):
            """Validate, calculate BS, upsert into history, persist."""
            try:
                datetime.strptime(date_str, "%Y-%m-%d")
                if spot <= 0 or vol <= 0:
                    raise ValueError("Spot and Vol must be positive.")
                bs_price = _calc_bs(date_str, spot, vol)
            except ValueError as exc:
                messagebox.showerror("Input Error", str(exc), parent=popup)
                return False
            target_date = original_date or date_str
            existing = next(
                (h for h in pos["mtm_history"] if h["date"] == target_date), None)
            if existing:
                existing.update(date=date_str, spot=spot,
                                vol=vol, bs_price=bs_price)
            else:
                pos["mtm_history"].append(
                    dict(date=date_str, spot=spot, vol=vol,
                         bs_price=bs_price, daily_pnl=0.0, cum_pnl=0.0))
            _recalc_all()
            self._otc_refresh_tree()
            self._otc_save_book_silent()
            refresh_tree()
            refresh_chart()
            return True

        def _read_form():
            try:
                return (entry_date_var.get().strip(),
                        float(entry_spot_var.get()),
                        float(entry_vol_var.get()))
            except ValueError:
                messagebox.showerror("Input Error",
                                     "Date must be YYYY-MM-DD; "
                                     "Spot and Vol must be numbers.",
                                     parent=popup)
                return None

        def _add_entry():
            vals = _read_form()
            if not vals:
                return
            date_str, spot, vol = vals
            if any(h["date"] == date_str for h in pos["mtm_history"]):
                if not messagebox.askyesno(
                        "Overwrite?",
                        f"Entry for {date_str} already exists. Overwrite?",
                        parent=popup):
                    return
            _commit(date_str, spot, vol)

        def _update_entry():
            rows = hist_tree.selection()
            if not rows:
                messagebox.showwarning(
                    "No Selection", "Click a row to edit first.", parent=popup)
                return
            original_date = hist_tree.item(rows[0], "values")[0]
            vals = _read_form()
            if vals:
                _commit(vals[0], vals[1], vals[2], original_date=original_date)

        def _delete_entry():
            rows = hist_tree.selection()
            if not rows:
                messagebox.showwarning(
                    "No Selection", "Click a row to delete first.", parent=popup)
                return
            date_str = hist_tree.item(rows[0], "values")[0]
            if messagebox.askyesno("Confirm Delete",
                                   f"Delete MTM entry for {date_str}?",
                                   parent=popup):
                pos["mtm_history"] = [
                    h for h in pos["mtm_history"] if h["date"] != date_str]
                _recalc_all()
                self._otc_refresh_tree()
                self._otc_save_book_silent()
                refresh_tree()
                refresh_chart()

        # ── Initial render ──
        refresh_tree()
        refresh_chart()

        ttk.Button(popup, text="Close",
                   command=lambda: (plt.close(fig), popup.destroy())
                   ).pack(pady=6)
        popup.protocol("WM_DELETE_WINDOW",
                       lambda: (plt.close(fig), popup.destroy()))

    # ── Per-position MTM ─────────────────────────────────────────────────────

    def _otc_run_mtm_selected(self):
        sel = self._otc_tree.selection()
        if not sel:
            messagebox.showwarning("No Selection",
                                   "Select a position to MTM.")
            return
        pos = next((p for p in self.otc_positions if p["id"] == sel[0]), None)
        if not pos:
            return
        try:
            global_spot = float(self.current_price.get())
            global_vol  = float(self.volatility.get())
        except ValueError:
            messagebox.showerror(
                "MTM Error",
                "Set valid Spot and Vol in the Parameters panel first.")
            return

        today_str = datetime.now().strftime("%Y-%m-%d")
        try:
            exp    = datetime.strptime(pos["expiry_date"], "%Y-%m-%d")
            T_days = (exp - datetime.now()).days
            if T_days <= 0:
                messagebox.showwarning(
                    "MTM",
                    f"Position expired: "
                    f"{pos['counterparty']}/{pos['underlying']}")
                return
            S           = pos["spot_override"] if pos.get("spot_override") else global_spot
            vol, new_atm = self._otc_resolve_vol(pos, S, T_days, global_vol)
            pos["surface_atm_vol"] = new_atm
            bs_price, *_ = self._black_scholes_price(
                S, pos["strike"], T_days / 365.0,
                pos.get("rate", 0.05), vol, pos["opt_type"])
            cum_pnl   = self._otc_cum_pnl(pos, S, bs_price)
            prev_cum  = (pos["mtm_history"][-1]["cum_pnl"]
                         if pos["mtm_history"] else 0.0)
            daily_pnl = cum_pnl - prev_cum
            entry = dict(date=today_str, spot=S, vol=vol,
                         atm_vol=new_atm, bs_price=bs_price,
                         daily_pnl=daily_pnl, cum_pnl=cum_pnl)
            if (pos["mtm_history"] and
                    pos["mtm_history"][-1]["date"] == today_str):
                pos["mtm_history"][-1] = entry
            else:
                pos["mtm_history"].append(entry)
            self._otc_refresh_tree()
            self._otc_save_book_silent()
            self._update_status(
                f"MTM: {pos['counterparty']} {pos['underlying']} "
                f"{pos['opt_type'].upper()} K={pos['strike']:.2f}  "
                f"BS={bs_price:.4f}  Cum P&L={cum_pnl:+,.2f}")
        except Exception as exc:
            messagebox.showerror("MTM Error", str(exc))

    # ── OTC persistence ──────────────────────────────────────────────────────

    def _otc_save_book_silent(self):
        try:
            with open(self._otc_book_path, "w", encoding="utf-8") as fh:
                json.dump(self.otc_positions, fh, indent=2)
        except Exception as exc:
            self._update_status(f"OTC auto-save error: {exc}")

    def _otc_save_book(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile="otc_book.json",
            title="Save OTC Book",
        )
        if path:
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(self.otc_positions, fh, indent=2)
                self._otc_book_path = path
                self._update_status(
                    f"OTC book saved: {len(self.otc_positions)} position(s)  →  {path}")
            except Exception as exc:
                messagebox.showerror("Save Error", str(exc))

    def _load_otc_book(self):
        if os.path.exists(self._otc_book_path):
            try:
                with open(self._otc_book_path, encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, list):
                    self.otc_positions = data
                    self._otc_refresh_tree()
                    self._update_status(
                        f"OTC book loaded: {len(data)} position(s)")
            except Exception as exc:
                self._update_status(f"OTC book load error: {exc}")

    def _otc_load_book(self):
        path = filedialog.askopenfilename(
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Open OTC Book",
        )
        if path:
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, list):
                    self.otc_positions = data
                    self._otc_book_path = path
                    self._otc_refresh_tree()
                    self._update_status(
                        f"OTC book loaded: {len(data)} position(s)  ←  {path}")
                else:
                    messagebox.showerror(
                        "Load Error", "File does not contain a valid OTC book.")
            except Exception as exc:
                messagebox.showerror("Load Error", str(exc))

    # ──────────────────────── IB METHODS ─────────────────────────────────────

    def _connect_ib(self):
        if not IB_AVAILABLE:
            messagebox.showwarning("ibapi Missing",
                                   "ibapi is not installed.\n"
                                   "Install it with:  pip install ibapi")
            return
        host = self.host_var.get().strip()
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            messagebox.showerror("Invalid Port", "Port must be an integer.")
            return

        self._update_status(f"Connecting to {host}:{port} …")

        def _thread_target():
            try:
                self.ib_app.connect(host, port, clientId=0)
                self.ib_app.run()          # blocks until disconnected
            except Exception as exc:
                self._update_status(f"Connection error: {exc}")

        t = threading.Thread(target=_thread_target, daemon=True)
        t.start()

        # Poll for connection confirmation (max 5 s)
        def _poll(attempts=0):
            if self.ib_app.connected:
                self.ib_connected = True
                self.connect_btn   .config(state="disabled")
                self.disconnect_btn.config(state="normal")
                self.fetch_btn     .config(state="normal")
                self.conn_status_lbl.config(text="● CONNECTED", fg="#44cc77")
                self._update_status(f"Connected to IB TWS at {host}:{port}")
            elif attempts < 50:          # 50 × 100 ms = 5 s
                self.root.after(100, lambda: _poll(attempts + 1))
            else:
                self._update_status("Timed out – could not connect to IB TWS.")

        self.root.after(200, _poll)

    def _disconnect_ib(self):
        try:
            self.ib_app.disconnect()
        except Exception:
            pass
        self.ib_app.connected = False
        self.ib_connected = False
        self.connect_btn   .config(state="normal")
        self.disconnect_btn.config(state="disabled")
        self.fetch_btn     .config(state="disabled")
        self.conn_status_lbl.config(text="● DISCONNECTED", fg="#cc4444")
        self._update_status("Disconnected from IB TWS.")

    def _create_equity_contract(self, symbol: str):
        contract = Contract()
        contract.symbol   = symbol
        contract.secType  = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"
        return contract

    def _fetch_market_data(self):
        if not self.ib_connected:
            messagebox.showwarning("Not Connected",
                                   "Please connect to IB TWS first.")
            return

        ticker = self.ticker_var.get().strip().upper()
        self._update_status(f"Fetching market data for {ticker} …")

        self.ib_app.historical_data.clear()
        contract = self._create_equity_contract(ticker)
        end_dt   = datetime.now().strftime("%Y%m%d %H:%M:%S")

        # req 1 – price  (5 trading days)
        try:
            self.ib_app.reqHistoricalData(
                reqId=1, contract=contract,
                endDateTime=end_dt, durationStr="5 D",
                barSizeSetting="1 day", whatToShow="TRADES",
                useRTH=1, formatDate=1, keepUpToDate=False, chartOptions=[])
        except Exception as exc:
            self._update_status(f"Price request error: {exc}")

        # req 2 – implied vol  (1 month)
        try:
            self.ib_app.reqHistoricalData(
                reqId=2, contract=contract,
                endDateTime=end_dt, durationStr="1 M",
                barSizeSetting="1 day",
                whatToShow="OPTION_IMPLIED_VOLATILITY",
                useRTH=1, formatDate=1, keepUpToDate=False, chartOptions=[])
        except Exception as exc:
            self._update_status(f"Vol request error: {exc}")

        # Process after 3 s data window
        self.root.after(3000, lambda: self._process_market_data(ticker))

    def _process_market_data(self, ticker: str):
        hist = self.ib_app.historical_data

        if 1 in hist and hist[1]:
            self._spot_from_ib = hist[1][-1]["close"]
            self.current_price.set(self._spot_from_ib)
            self.fetched_price_var.set(f"Spot:  ${self._spot_from_ib:.2f}")
            self._update_status(
                f"{ticker} spot price: ${self._spot_from_ib:.2f}")
        else:
            self._update_status("No price data received from IB.")

        if 2 in hist and hist[2]:
            raw_vol = hist[2][-1]["close"]
            # IB returns vol as a percentage (e.g. 23.4 means 23.4%)
            self._vol_from_ib = raw_vol / 1  
            self.volatility.set(self._vol_from_ib)
            self.fetched_vol_var.set(f"IV:    {self._vol_from_ib:.2%}")
            self._update_status(
                f"{ticker} implied vol: {self._vol_from_ib:.2%}")
        else:
            self._update_status("No IV data received from IB.")

    # ───────────────────── BLACK-SCHOLES MATH ────────────────────────────────

    def _black_scholes_price(self, S, K, T, r, sigma, option_type="call"):
        """Return (price, d1, d2). Raises ValueError on bad inputs."""
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            raise ValueError("S, K, T and sigma must be positive.")
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)
        if option_type == "call":
            price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
        else:
            price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        return price, d1, d2

    def _greeks(self, S, K, T, r, sigma, option_type="call"):
        """Return (price, delta, gamma, vega, theta_daily, rho, d1, d2)."""
        price, d1, d2 = self._black_scholes_price(S, K, T, r, sigma, option_type)
        pdf_d1 = norm.pdf(d1)

        delta = norm.cdf(d1)  if option_type == "call" else -norm.cdf(-d1)
        gamma = pdf_d1 / (S * sigma * np.sqrt(T))
        vega  = S * pdf_d1 * np.sqrt(T) / 100.0   # per 1 pp vol move

        if option_type == "call":
            theta_yr = (
                -S * pdf_d1 * sigma / (2 * np.sqrt(T))
                - r * K * np.exp(-r * T) * norm.cdf(d2)
            )
            rho = K * T * np.exp(-r * T) * norm.cdf(d2)
        else:
            theta_yr = (
                -S * pdf_d1 * sigma / (2 * np.sqrt(T))
                + r * K * np.exp(-r * T) * norm.cdf(-d2)
            )
            rho = -K * T * np.exp(-r * T) * norm.cdf(-d2)

        return price, delta, gamma, vega, theta_yr / 365.0, rho, d1, d2

    # ──────────────────────── BUTTON CALLBACKS ───────────────────────────────

    def _on_calculate(self):
        try:
            S     = float(self.current_price.get())
            K     = float(self.strike_price.get())
            T_day = float(self.time_to_expiry.get())
            r     = float(self.risk_free_rate.get())
            sig   = float(self.volatility.get())
            opt   = self.option_type.get()
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("Input Error", str(exc))
            return

        T = T_day / 365.0
        try:
            price, delta, gamma, vega, theta, rho, d1, d2 = self._greeks(
                S, K, T, r, sig, opt)
        except ValueError as exc:
            messagebox.showerror("Calculation Error", str(exc))
            return

        self.current_premium_var.set(f"${price:.4f}")
        self.current_delta_var  .set(f"{delta:+.4f}")
        self.current_gamma_var  .set(f"{gamma:.6f}")
        self.current_vega_var   .set(f"{vega:.4f}")
        self.current_theta_var  .set(f"{theta:+.6f}")
        self.current_rho_var    .set(f"{rho:+.4f}")

        # Pre-fill scenario with current values
        if not self.new_spot_price_var.get():
            self.new_spot_price_var.set(f"{S:.2f}")
        if not self.new_volatility_var.get():
            self.new_volatility_var.set(f"{sig:.4f}")

        # Auto-set premium paid in payoff chart
        self.prem_paid_var.set(f"{price:.4f}")

        self._update_status(
            f"Calculated: {opt.upper()} K={K:.2f} T={T_day:.0f}d  "
            f"Price=${price:.4f}  Δ={delta:+.4f}  Γ={gamma:.6f}")
        self._refresh_payoff()
        self._refresh_vol_surface()
        self._refresh_strategy()

    def _analyze_scenario(self):
        try:
            new_S   = float(self.new_spot_price_var.get())
            new_sig = float(self.new_volatility_var.get())
        except ValueError:
            messagebox.showerror("Input Error",
                                 "Enter numeric values for new spot and volatility.")
            return
        try:
            K     = float(self.strike_price.get())
            T_day = float(self.time_to_expiry.get())
            r     = float(self.risk_free_rate.get())
            opt   = self.option_type.get()
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("Input Error", str(exc))
            return

        T = T_day / 365.0
        try:
            price, delta, gamma, vega, theta, rho, *_ = self._greeks(
                new_S, K, T, r, new_sig, opt)
        except ValueError as exc:
            messagebox.showerror("Calculation Error", str(exc))
            return

        self.new_premium_var.set(f"${price:.4f}")
        self.new_delta_var  .set(f"{delta:+.4f}")
        self.new_gamma_var  .set(f"{gamma:.6f}")
        self.new_vega_var   .set(f"{vega:.4f}")
        self.new_theta_var  .set(f"{theta:+.6f}")
        self.new_rho_var    .set(f"{rho:+.4f}")
        self._update_status(
            f"Scenario: S={new_S:.2f} σ={new_sig:.2%} → "
            f"{opt.upper()} ${price:.4f}  Δ={delta:+.4f}")

    # ────────────────────────── HELPERS ──────────────────────────────────────

    def _update_status(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.status_var.set(f"[{ts}]  {msg}")
        self.root.update_idletasks()


# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    app = OptionPricingCalculator(root)
    root.mainloop()
