import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
from datetime import datetime, timedelta
import requests
import json
from pathlib import Path

import positions as posmod

try:
    import yfinance as yf
    HAS_YF = True
except Exception:
    HAS_YF = False


st.set_page_config(page_title="M Wealth | Asset Allocation", layout="wide")

# =========================
# CSS (layout)
# =========================
st.markdown(
    """
    <style>
    .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
    div[data-testid="stMetricValue"] { font-size: 1.4rem; }
    div[data-testid="stMetricDelta"] { font-size: 0.9rem; }
    .mw-subtle { color: rgba(250,250,250,0.65); font-size: 0.9rem; }
    .mw-divider { border-top: 1px solid rgba(255,255,255,0.08); margin: 0.75rem 0 1rem 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

# =========================
# Utils
# =========================
def safe_int(val):
    try:
        return int(float(str(val).strip().replace(",", ".")))
    except:
        return 0

def format_brl(v):
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return "R$ 0,00"

def format_usd(v):
    try:
        return f"US$ {float(v):,.2f}"
    except:
        return "US$ 0.00"

def fmt_pct(x):
    try:
        return f"{100*float(x):.2f}%"
    except:
        return "0.00%"

def parse_input_money(s):
    try:
        return float(
            str(s)
            .replace("R$", "")
            .replace("US$", "")
            .replace(".", "")
            .replace(",", ".")
            .strip()
        )
    except:
        return 0.0

def style_compra_venda(val):
    try:
        num = float(
            str(val)
            .replace("R$", "")
            .replace("US$", "")
            .replace(".", "")
            .replace(",", ".")
            .strip()
        )
    except:
        return ""
    if num > 0:
        return "color: #2e7d32; font-weight: 650;"  # buy
    if num < 0:
        return "color: #c62828; font-weight: 650;"  # sell
    return "color: rgba(255,255,255,0.55);"

# =========================
# Labels (display clean)
# =========================
DISPLAY_BUCKET = {
    "Bancário Pré": "Bancário",
    "Tesouro Pré": "Tesouro",
}
def disp(nome: str) -> str:
    return DISPLAY_BUCKET.get(nome, nome)

# =========================
# PTAX (BCB)
# =========================
@st.cache_data(ttl=3600)
def get_ptax_usdbrl_last():
    base = "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoDolarPeriodo"
    hoje = datetime.now().date()
    ini = hoje - timedelta(days=10)
    data_ini = ini.strftime("%m-%d-%Y")
    data_fim = hoje.strftime("%m-%d-%Y")

    url = (
        f"{base}(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
        f"?@dataInicial='{data_ini}'&@dataFinalCotacao='{data_fim}'"
        f"&$format=json&$select=cotacaoVenda,dataHoraCotacao&$orderby=dataHoraCotacao desc&$top=1"
    )

    r = requests.get(url, timeout=20)
    r.raise_for_status()
    js = r.json()
    val = js.get("value", [])
    if not val:
        raise ValueError("Sem dados PTAX no período.")
    return float(val[0]["cotacaoVenda"]), val[0]["dataHoraCotacao"]

# =========================
# Excel pesos
# =========================
@st.cache_data
def load_pesos_xlsx(path_xlsx: str = "Pesos-alocacao.xlsx"):
    xls = pd.ExcelFile(path_xlsx, engine="openpyxl")
    sheet0 = xls.sheet_names[0]
    df = pd.read_excel(xls, sheet_name=sheet0, header=None).fillna("")
    pesos = {}
    carteira_atual = None
    for _, row in df.iterrows():
        a = str(row.iloc[0]).strip()
        b = str(row.iloc[1]).strip()
        if a == "" and b == "":
            continue
        if b.lower() == "neutro" and a != "":
            carteira_atual = a
            pesos.setdefault(carteira_atual, {})
            continue
        if carteira_atual is None:
            continue
        bucket = a
        if bucket == "":
            continue
        try:
            w = float(str(row.iloc[1]).replace(",", ".").strip())
        except:
            w = 0.0
        pesos[carteira_atual][bucket] = w
    return {k: v for k, v in pesos.items() if len(v) > 0}

# =========================
# Regras
# =========================
RF_BR_BUCKETS = [
    ("RF Pós", "Imediato"),
    ("RF Pós", "1 a 30 dias"),
    ("RF Pós", "31 a 180 dias"),
    ("RF Pós", "181 a 360 dias"),
    ("RF Pós", "361+ dias"),
    ("RF Pós", "FiInfra e Cetipados"),
    ("RF Pré", "Bancário Pré"),
    ("RF Pré", "Tesouro Pré"),
    ("RF Inflação", "Bancário"),
    ("RF Inflação", "Tesouro"),
    ("RF Inflação", "FiInfra e Cetipado"),
    ("RF Inflação", "Crédito Privado"),
]

def macro_weights_from_neutro(p):
    rv_br = float(p.get("RV Brasil", 0.0))
    intl = float(p.get("Internacional", p.get("Internacional ", 0.0)))
    intl_rf = float(p.get("Renda Fixa", 0.0))
    intl_rv = float(p.get("Renda Variável", 0.0))
    rf_br = max(0.0, 1.0 - rv_br - intl)
    return rf_br, rv_br, intl, intl_rf, intl_rv

def rf_buckets_ideal(valor_total_brl: float, pesos_neutro: dict):
    out = {}
    for pai, filho in RF_BR_BUCKETS:
        w = float(pesos_neutro.get(filho, 0.0))
        out[f"{pai} > {filho}"] = valor_total_brl * w
    return out

# =========================
# Tickers
# =========================
RV_BR_ACOES = ["CPLE3", "EGIE3", "AXIA3", "ITUB4", "VALE3", "ALOS3", "FLRY3", "ABEV3", "PRIO3", "WEGE3"]
RV_BR_FIIS = ["KNRI11", "XPML11", "HGLG11", "PVBI11", "HGRU11", "KNCR11", "KNIP11", "KNCA11"]
RV_INT = ["VOO", "VOOG", "VIOV"]

def equal_weights(tickers):
    if not tickers:
        return {}
    w = 1.0 / len(tickers)
    return {t: w for t in tickers}

# =========================
# RV engine (yfinance)
# =========================
def calcular_rv_yfinance(nome_bloco: str, valor_total: float, pesos_ticker: dict, moeda: str, add_sa_suffix: bool):
    if valor_total <= 0 or not pesos_ticker:
        st.info(f"{nome_bloco}: sem alocação.")
        return
    if not HAS_YF:
        st.error("yfinance não está disponível. Verifique requirements.txt e reinicie o app.")
        return

    fmt_money = format_brl if moeda == "BRL" else format_usd
    st.markdown(f"**Valor ideal do bloco:** {fmt_money(valor_total)}")

    col1, col2 = st.columns([1.1, 2.2], gap="large")
    qtd_input = {}

    with col1:
        st.markdown("Quantidade atual (provisório):")
        for t in pesos_ticker.keys():
            qtd_input[t] = st.text_input(
                t,
                value=st.session_state.get(f"qtd_{nome_bloco}_{t}", ""),
                key=f"qtd_{nome_bloco}_{t}",
            )

    ativos = list(pesos_ticker.keys())
    precos, ativos_ok, pesos_ok, qt_atual_ok = [], [], [], []

    for t in ativos:
        try:
            yf_ticker = f"{t}.SA" if add_sa_suffix else t
            hist = yf.Ticker(yf_ticker).history(period="1d")
            preco = hist["Close"].iloc[-1] if (hist is not None and not hist.empty) else None
            if preco is None or pd.isna(preco) or float(preco) <= 0:
                continue
            precos.append(float(preco))
            ativos_ok.append(t)
            pesos_ok.append(float(pesos_ticker[t]))
            qt_atual_ok.append(safe_int(qtd_input.get(t, 0)))
        except Exception:
            continue

    if not ativos_ok:
        st.error("Nenhum ativo com preço válido foi encontrado no yfinance.")
        return

    precos = np.array(precos, dtype=float)
    pesos_ok = np.array(pesos_ok, dtype=float)
    pesos_ok = pesos_ok / pesos_ok.sum()

    valor_ideal = valor_total * pesos_ok
    qt_ideal = np.nan_to_num(valor_ideal / precos, nan=0, posinf=0, neginf=0).astype(int)
    qt_atual = np.array(qt_atual_ok, dtype=int)
    delta = qt_ideal - qt_atual
    acao = np.where(delta > 0, "Comprar", np.where(delta < 0, "Vender", "-"))

    df = pd.DataFrame({
        "Ativo": ativos_ok,
        "Preço": [format_brl(p) if moeda == "BRL" else f"{p:.2f}" for p in precos],
        "Peso": [fmt_pct(w) for w in pesos_ok],
        "Qtd ideal": qt_ideal,
        "Qtd atual": qt_atual,
        "Ação": acao,
        "Diferença": delta,
        "Valor ideal": [fmt_money(v) for v in valor_ideal],
        "Valor atual": [fmt_money(p*q) for p, q in zip(precos, qt_atual)],
    })

    with col2:
        st.dataframe(
            df.style.applymap(style_compra_venda, subset=["Diferença"]),
            use_container_width=True,
            height=540,
            hide_index=True,
        )

    impacto = float(np.sum(precos * qt_ideal) - np.sum(precos * qt_atual))
    st.markdown(f"**Impacto financeiro estimado:** {fmt_money(impacto)}")

    df_export = pd.DataFrame({
        "Ativo": ativos_ok,
        "C/V": np.where(delta > 0, "C", np.where(delta < 0, "V", "-")),
        "Quantidade": np.abs(delta),
        "Preço": precos,
    })

    out = BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df_export.to_excel(writer, sheet_name="Basket", index=False)

    st.download_button(
        label=f"Baixar Basket ({nome_bloco}).xlsx",
        data=out.getvalue(),
        file_name=f"basket_{nome_bloco}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# =========================
# Header (logo + título)
# =========================
h1, h2 = st.columns([0.14, 0.86], vertical_alignment="center")
with h1:
    try:
        st.image("Logo-M-Wealth.png", use_container_width=True)
    except Exception:
        pass
with h2:
    st.markdown("## Asset Allocation")
    st.markdown('<div class="mw-subtle">Protótipo: inputs atuais serão substituídos por posição do cliente</div>', unsafe_allow_html=True)
    st.markdown('<div class="mw-divider"></div>', unsafe_allow_html=True)

# =========================
# Tabs
# =========================
tab_up, tab_aa, tab_teorica = st.tabs([
    "Atualizar posições", 
    "Asset Allocation", 
    "Carteira Teórica"
])


with tab_up:
    st.subheader("Posições lidas do GitHub")
    st.caption("O app lê os arquivos na pasta ./posicoes. Para atualizar, faça commit de novos arquivos com o mesmo nome.")

    st.markdown("## Posições consolidadas")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        dtpos = datetime.now().date()
        st.write(f"Data de posição padrão: {dtpos.isoformat()}")
    
    with col2:
        rebuild = st.button("Rebuild latest agora", type="primary")
    
    if rebuild:
        with st.spinner("Lendo arquivos → normalizando → merge controle → salvando..."):
            try:
                df_latest = posmod.build_latest_from_repo(dt_posicao=dtpos.isoformat())
                st.success(f"Rebuild OK! Linhas: {len(df_latest)}")
    
                # guarda para uso abaixo
                st.session_state.df_latest = df_latest
    
            except Exception as e:
                st.error(f"Erro no rebuild: {type(e).__name__} - {e}")
                st.exception(e)
    
    # Seletor de carteira/conta baseado no último rebuild
    if "df_latest" in st.session_state:
        df_latest = st.session_state.df_latest
    
        st.markdown("### Filtro por carteira / conta")
    
        col_a, col_b = st.columns(2)
    
        with col_a:
            carteiras = ["Todas"] + sorted(df_latest["GRUPO GERAL"].dropna().unique().tolist())
            carteira_sel = st.selectbox("Carteira (GRUPO GERAL)", carteiras, key="sel_carteira")
    
        with col_b:
            if carteira_sel != "Todas":
                df_filt = df_latest[df_latest["GRUPO GERAL"] == carteira_sel]
            else:
                df_filt = df_latest.copy()
    
            contas = ["Todas"] + sorted(df_filt["CLIENTE - CORRETORA"].dropna().unique().tolist())
            conta_sel = st.selectbox("Conta (CLIENTE - CORRETORA)", contas, key="sel_conta")
    
        if carteira_sel != "Todas":
            df_filt = df_latest[df_latest["GRUPO GERAL"] == carteira_sel]
        else:
            df_filt = df_latest.copy()
    
        if conta_sel != "Todas":
            df_filt = df_filt[df_filt["CLIENTE - CORRETORA"] == conta_sel]
    
        if len(df_filt) > 0:
            total_sel = float(df_filt["valor_mercado"].sum())
            st.metric("Total selecionado", f"R$ {total_sel:,.2f}")
    
            st.markdown("#### Ativos da seleção")
            cols_sel = [
                "asset_tipo", "asset_id", "asset_nome",
                "valor_mercado", "corretora", "conta"
            ]
            cols_sel = [c for c in cols_sel if c in df_filt.columns]
            st.dataframe(
                df_filt[cols_sel].sort_values("valor_mercado", ascending=False),
                use_container_width=True,
                height=420,
            )

with tab_aa:
    st.markdown("### 🎯 Asset Allocation - Cliente Real")
    
    # Sidebar com seleção cliente + auto-preenchimento
    with st.sidebar:
        st.markdown("### 👥 Selecione Cliente")
        if "dflatest" in st.session_state:
            dflatest = st.session_state["dflatest"]
            carteiras = ["Todas"] + sorted(dflatest["GRUPO GERAL"].dropna().unique().tolist())
            carteira_sel = st.selectbox("Carteira:", carteiras)
            
            if carteira_sel != "Todas":
                pos_carteira = dflatest[dflatest["GRUPO GERAL"] == carteira_sel]
                contas = sorted(pos_carteira["CLIENTE - CORRETORA"].dropna().unique())
                conta_sel = st.selectbox("Conta:", contas)
                pos_real = pos_carteira[pos_carteira["CLIENTE - CORRETORA"] == conta_sel]
            else:
                pos_real = dflatest
                conta_sel = "Todas"
            
            total_real = pos_real["valor_mercado"].sum()
            st.metric("💰 Total Real", f"R$ {total_real:,.0f}")
            
            # Posições atuais por bucket
            if "bucket_estrategia" in pos_real.columns:
                buckets = pos_real.groupby("bucket_estrategia")["valor_mercado"].sum()
                for b, v in buckets.items():
                    st.caption(f"📊 {b}: R$ {v:,.0f}")
            
            st.success("✅ Posições carregadas!")
            st.session_state.patrimonio_brl = total_real
            st.session_state.carteira_sel = carteira_sel
            st.session_state.conta_sel = conta_sel
        else:
            st.warning("⚠️ Faça 'Rebuild' na aba Atualizar Posições primeiro")
            st.session_state.patrimonio_brl = 500000
    
    # Usa o valor carregado
    patrimonio_brl = st.session_state.get("patrimonio_brl", 500000)
    
    # Resto do código de cálculo (mantém igual)...
    # [load_pesos_xlsx, métricas, expanders RF/RV/INT]

    # Sidebar COMPLETA
    with st.sidebar:
        st.markdown("### 📊 Cliente & Parâmetros")
        
        # 1. Auto-preenchimento cliente real
        if "dflatest" in st.session_state:
            dflatest = st.session_state["dflatest"]
            carteiras = sorted(dflatest["GRUPO GERAL"].dropna().unique())
            carteira_real = st.selectbox("👥 Carteira:", carteiras)
            
            pos_carteira = dflatest[dflatest["GRUPO GERAL"] == carteira_real]
            contas = sorted(pos_carteira["CLIENTE - CORRETORA"].dropna().unique())
            conta_real = st.selectbox("🏦 Conta:", contas)
            
            pos_real = pos_carteira[pos_carteira["CLIENTE - CORRETORA"] == conta_real]
            pl_real = pos_real["valor_mercado"].sum()  # Usa sempre valor_mercado
            
            st.metric("💰 Patrimônio", f"R$ {pl_real:,.0f}")
            st.caption(f"{len(pos_real)} ativos")
            
            patrimonio_brl = pl_real
            modelo_sel = "Neutro"  # Default
            st.success("✅ Carregado!")
        else:
            patrimonio_brl = st.number_input("💰 Patrimônio R$", value=500000, step=10000)
            modelo_sel = "Neutro"
        
        # 2. PTAX simplificado
        try:
            ptax, _ = get_ptax_usdbrl_last()
            usd_brl = ptax
            st.caption(f"💱 PTAX: R$ {usd_brl:.4f}")
        except:
            usd_brl = 5.60
            st.caption("💱 PTAX: R$ 5.60 (manual)")
        
        # 3. Modelo
        try:
            pesos_manual = load_pesos_xlsx()
            carteiras_modelo = list(pesos_manual.keys())
            modelo_sel = st.selectbox("🎯 Modelo alvo:", carteiras_modelo)
        except:
            st.info("📊 Pesos-alocacao.xlsx não encontrado")
        
        st.markdown("---")

    # 3) Cálculo ideal (mantive seu motor)
    # Cálculo das métricas com proteção
    try:
        p = pesos_manual[modelo_sel]
        rfbrw, rvbrw, intlw, intlrfw, intlrvw = macro_weights_from_neutro(p)
    except:
        rfbrw, rvbrw, intlw, intlrfw, intlrvw = 0.6, 0.2, 0.2, 0.15, 0.05  # Default Neutro
    
    # Valores calculados (mantém o resto igual)
    alocavel_brl = max(0.0, patrimonio_brl)
    valor_rv_total_brl = alocavel_brl * rvbrw
    valor_int_total_brl = alocavel_brl * intlw
    valor_int_total_usd = valor_int_total_brl / usd_brl if usd_brl else 0.0
    valor_rf_br_brl = max(0.0, alocavel_brl - valor_rv_total_brl - valor_int_total_brl)
    
    # Métricas (mantém igual)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Patrimônio R$", format_brl(alocavel_brl))
    c2.metric("RF Brasil", format_brl(valor_rf_br_brl), delta=fmt_pct(rfbrw))
    c3.metric("RV Brasil", format_brl(valor_rv_total_brl), delta=fmt_pct(rvbrw))
    c4.metric("Internacional", format_brl(valor_int_total_brl), delta=fmt_pct(intlw))

    st.markdown('<div class="mw-divider"></div>', unsafe_allow_html=True)

    with st.expander("1) Renda Fixa Brasil (R$)", expanded=True):
        st.markdown(f"Macro RF Brasil: {format_brl(valor_rf_br_brl)}")
        ideal_raw = rf_buckets_ideal(alocavel_brl, p)

        colin, colout = st.columns([1.05, 1.95], gap="large")
        rf_atual = {}
        with colin:
            st.markdown("Valores atuais (provisório) por bucket:")
            for k in ideal_raw.keys():
                rf_atual[k] = parse_input_money(st.text_input(k, value=""))

        rows = []
        for k, v_ideal in ideal_raw.items():
            v_atual = float(rf_atual.get(k, 0.0))
            rows.append([k, v_ideal, v_atual, v_ideal - v_atual, v_ideal / alocavel_brl if alocavel_brl else 0.0])

        dfrf = pd.DataFrame(rows, columns=["Bucket", "Ideal", "Atual", "Comprar/Vender", "Peso"])
        dfrf_fmt = dfrf.copy()
        dfrf_fmt["Ideal"] = dfrf_fmt["Ideal"].apply(format_brl)
        dfrf_fmt["Atual"] = dfrf_fmt["Atual"].apply(format_brl)
        dfrf_fmt["Comprar/Vender"] = dfrf_fmt["Comprar/Vender"].apply(format_brl)
        dfrf_fmt["Peso"] = dfrf["Peso"].apply(fmt_pct)

        with colout:
            st.dataframe(
                dfrf_fmt.style.applymap(style_compra_venda, subset=["Comprar/Vender"]),
                use_container_width=True,
                height=600,
                hide_index=True,
            )

    with st.expander("2) Renda Variável Brasil (R$)", expanded=True):
        st.markdown(f"Macro RV Brasil: {format_brl(valor_rv_total_brl)}")
        tab1, tab2 = st.tabs(["Ações", "FIIs"])
        with tab1:
            calcular_rv_yfinance("rvbr_acoes", valor_rv_total_brl, equal_weights(RV_BR_ACOES), moeda="BRL", add_sa_suffix=True)
        with tab2:
            calcular_rv_yfinance("rvbr_fiis", valor_rv_total_brl, equal_weights(RV_BR_FIIS), moeda="BRL", add_sa_suffix=True)

    with st.expander("3) Internacional (US$)", expanded=True):
        st.markdown(f"Macro Internacional: {format_usd(valor_int_total_usd)} ({format_brl(valor_int_total_brl)})")
        st.info("Internacional RF/RV ainda está simplificado neste protótipo.")
        calcular_rv_yfinance("int_rv", valor_int_total_usd, equal_weights(RV_INT), moeda="USD", add_sa_suffix=False)
        
with tab_teorica:
        st.markdown("###Simulação Carteira Teórica")
    
col1, col2 = st.columns([1, 3])
with col1:
        try:
            pesos = load_pesos_xlsx()
            carteiras = list(pesos.keys())
            carteira_sel = st.selectbox("Modelo:", carteiras)
            valor_simul = st.number_input("Valor R$", value=1000000, step=50000)
        except:
            st.error("Pesos-alocacao.xlsx não encontrado")
            st.stop()
    
with col2:
        # Macro alocação
        p = pesos[carteira_sel]
        rf_br, rv_br, intl = macro_weights_from_neutro(p)[:3]
        
        c1, c2, c3 = st.columns(3)
        c1.metric("RF Brasil", f"{rf_br:.1%}", f"R$ {rf_br*valor_simul:,.0f}")
        c2.metric("RV Brasil", f"{rv_br:.1%}", f"R$ {rv_br*valor_simul:,.0f}")
        c3.metric("Internacional", f"{intl:.1%}", f"R$ {intl*valor_simul:,.0f}")
        
        # Detalhe RF
        rf_buckets = rf_buckets_ideal(valor_simul * rf_br, p)
        df_rf = pd.DataFrame([
            {"Bucket": k, "Valor": v, "%": v/valor_simul*100} 
            for k,v in rf_buckets.items()
        ])
        st.dataframe(
            df_rf.style.format({"Valor": format_brl, "%": "{:.1f}%"}),
            use_container_width=True
        )
        
        st.caption("Para propostas comerciais")
    
