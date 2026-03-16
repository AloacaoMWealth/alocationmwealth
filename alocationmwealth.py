import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
from datetime import datetime, timedelta
import requests
import json
from pathlib import Path

import positions as posmod  # seu módulo positions.py

try:
    import yfinance as yf
    HAS_YF = True
except Exception:
    HAS_YF = False


st.set_page_config(page_title="M Wealth | Asset Allocation", layout="wide")

# =============================================================================
# CSS
# =============================================================================
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

# =============================================================================
# Funções utilitárias
# =============================================================================
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
        return "color: #2e7d32; font-weight: 650;"
    if num < 0:
        return "color: #c62828; font-weight: 650;"
    return "color: rgba(255,255,255,0.55);"

# =============================================================================
# PTAX
# =============================================================================
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

# =============================================================================
# Leitura dos pesos ideais
# =============================================================================
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

# =============================================================================
# Regras macro
# =============================================================================
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

# =============================================================================
# RV baskets (exemplo - ajuste conforme seu código original)
# =============================================================================
RV_BR_ACOES = ["ITUB4", "VALE3", "PETR4", "B3SA3", "WEGE3", "ABEV3", "BBAS3", "MGLU3"]
RV_BR_FIIS  = ["KNRI11", "HGLG11", "XPML11", "MXRF11", "VISC11", "HGRE11"]
RV_INT      = ["VOO", "QQQ", "SPY", "VTI", "VXUS"]

def equal_weights(tickers):
    if not tickers:
        return {}
    w = 1.0 / len(tickers)
    return {t: w for t in tickers}

# =============================================================================
# Função de sugestão RV (exemplo simplificado - mantenha sua versão real)
# =============================================================================
def calcular_rv_yfinance(key, target_value, weights, moeda="BRL", add_sa_suffix=False):
    st.write(f"**Sugestão {key.upper()}** (alvo: {format_brl(target_value) if moeda=='BRL' else format_usd(target_value)})")
    data = []
    for ticker, w in weights.items():
        t = ticker + ".SA" if add_sa_suffix else ticker
        try:
            info = yf.Ticker(t).info
            price = info.get("regularMarketPrice", info.get("previousClose", np.nan))
            if np.isnan(price):
                continue
            qtd = (target_value * w) / price
            data.append([ticker, round(qtd, 0), price, round(qtd * price, 2)])
        except:
            continue
    if data:
        df = pd.DataFrame(data, columns=["Ativo", "Qtd sugerida", "Preço", "Valor aproximado"])
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("Não foi possível obter cotações no momento.")

# =============================================================================
# Cabeçalho
# =============================================================================
st.title("M Wealth - Asset Allocation")
st.caption("Protótipo: posições reais × alocação teórica")

tab1, tab2, tab3 = st.tabs(["Atualizar posições", "Asset Allocation", "Carteira Teórica"])

# =============================================================================
# TAB 1 - Atualizar posições (mantenha sua implementação original aqui)
# =============================================================================
with tab1:
    st.header("Atualizar posições")
    if st.button("Rebuild latest positions"):
        with st.spinner("Reconstruindo posição consolidada..."):
            try:
                df = posmod.build_latest_from_repo()
                st.session_state["df_latest"] = df
                st.success("Posição consolidada com sucesso!")
                st.dataframe(df.head(10))
            except Exception as e:
                st.error(f"Erro ao reconstruir: {e}")

# =============================================================================
# TAB 2 - Asset Allocation (versão corrigida e melhorada)
# =============================================================================
with tab2:
    st.header("Asset Allocation - Cliente")

    if "df_latest" not in st.session_state:
        st.warning("Por favor, reconstrua as posições na aba anterior primeiro.")
        st.stop()

    df_latest = st.session_state.df_latest.copy()

    # Seleção do cliente (GRUPO GERAL)
    grupos = sorted(df_latest["GRUPO GERAL"].dropna().unique())
    grupo_selecionado = st.selectbox("Selecione o Grupo Geral (Cliente)", grupos)

    # Filtra posição do cliente (todas corretoras)
    pos_cliente = df_latest[df_latest["GRUPO GERAL"] == grupo_selecionado].copy()

    if pos_cliente.empty:
        st.error("Nenhuma posição encontrada para este grupo.")
        st.stop()

    pl_total = pos_cliente["valor_mercado"].sum()
    st.metric("Patrimônio Líquido Consolidado", format_brl(pl_total))

    # PTAX
    try:
        ptax, data_ptax = get_ptax_usdbrl_last()
        st.caption(f"PTAX ({data_ptax}): R$ {ptax:.4f}")
    except:
        ptax = 5.60
        st.caption("PTAX fallback: R$ 5.60")

    # Modelos disponíveis
    try:
        pesos = load_pesos_xlsx()
        modelos = list(pesos.keys())
        modelo_escolhido = st.selectbox("Modelo de alocação alvo", modelos, index=0)
    except:
        st.error("Não foi possível carregar Pesos-alocacao.xlsx")
        st.stop()

    p = pesos[modelo_escolhido]
    rf_br_w, rv_br_w, intl_w, intl_rf_w, intl_rv_w = macro_weights_from_neutro(p)

    alvo_rf_br  = pl_total * rf_br_w
    alvo_rv_br  = pl_total * rv_br_w
    alvo_intl   = pl_total * intl_w

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("RF Brasil", format_brl(alvo_rf_br), f"{rf_br_w:.1%}")
    col2.metric("RV Brasil", format_brl(alvo_rv_br), f"{rv_br_w:.1%}")
    col3.metric("Internacional", format_brl(alvo_intl), f"{intl_w:.1%}")
    col4.metric("Total alvo", format_brl(alvo_rf_br + alvo_rv_br + alvo_intl))

    # =============================================================================
    # Comparativo Atual x Ideal - MACRO
    # =============================================================================
    st.subheader("Comparativo Atual × Ideal (nível macro)")

    def classifica_macro(row):
        corretora = str(row.get("corretora", "")).upper()
        if corretora in ["CS", "CHARLES SCHWAB"]:
            return "Internacional"
        
        tipo = str(row.get("asset_tipo", "")).upper()
        mercado = str(row.get("mercado", "")).upper()
        sub_mercado = str(row.get("sub_mercado", "")).upper()
        
        if any(x in tipo + mercado + sub_mercado for x in ["AÇÃO", "FII", "RV", "EQUITY", "ETF", "STOCK"]):
            return "RV Brasil"
        return "RF Brasil"

    pos_cliente["macro"] = pos_cliente.apply(classifica_macro, axis=1)

    atual = pos_cliente.groupby("macro")["valor_mercado"].sum()
    atual = atual.reindex(["RF Brasil", "RV Brasil", "Internacional"]).fillna(0)

    comparativo = pd.DataFrame({
        "Categoria": ["RF Brasil", "RV Brasil", "Internacional"],
        "Atual (R$)": [format_brl(atual.get(c, 0)) for c in ["RF Brasil", "RV Brasil", "Internacional"]],
        "Alvo (R$)":  [format_brl(v) for v in [alvo_rf_br, alvo_rv_br, alvo_intl]],
        "Diferença (R$)": [format_brl(atual.get(c, 0) - v) for c,v in zip(
            ["RF Brasil", "RV Brasil", "Internacional"],
            [alvo_rf_br, alvo_rv_br, alvo_intl]
        )],
    })

    st.dataframe(
        comparativo.style.applymap(style_compra_venda, subset=["Diferença (R$)"]),
        use_container_width=True,
        hide_index=True
    )

    # =============================================================================
    # Sugestões RV (mantidas como exemplo)
    # =============================================================================
    with st.expander("Sugestão RV Brasil"):
        col_a, col_f = st.tabs(["Ações", "FIIs"])
        with col_a:
            calcular_rv_yfinance("Ações BR", alvo_rv_br * 0.7, equal_weights(RV_BR_ACOES), "BRL", True)
        with col_f:
            calcular_rv_yfinance("FIIs", alvo_rv_br * 0.3, equal_weights(RV_BR_FIIS), "BRL", True)

    with st.expander("Sugestão Internacional"):
        calcular_rv_yfinance("Internacional", alvo_intl / ptax, equal_weights(RV_INT), "USD", False)

# =============================================================================
# TAB 3 - Carteira Teórica
# =============================================================================
with tab3:
    st.header("Carteira Teórica (Simulação)")
    
    pesos_teor = load_pesos_xlsx()
    modelo_teor = st.selectbox("Modelo:", list(pesos_teor.keys()))
    valor_teor = st.number_input("Valor simulado (R$)", value=1_000_000, step=100_000)

    p_teor = pesos_teor[modelo_teor]
    rf, rv, intl = macro_weights_from_neutro(p_teor)[:3]

    c1, c2, c3 = st.columns(3)
    c1.metric("Renda Fixa", f"{rf:.0%}", format_brl(rf * valor_teor))
    c2.metric("Renda Variável BR", f"{rv:.0%}", format_brl(rv * valor_teor))
    c3.metric("Internacional", f"{intl:.0%}", format_brl(intl * valor_teor))

st.markdown("---")
st.caption(f"Última atualização: {datetime.now().strftime('%d/%m/%Y %H:%M')}")