import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from io import BytesIO

st.set_page_config(page_title="Asset Allocation (Novo)", layout="wide")

# -------------------------
# Utils (mantém estilo do seu app atual)
# -------------------------
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

def parse_input_money(s):
    try:
        return float(str(s).replace("R$", "").replace("US$", "").replace(".", "").replace(",", ".").strip())
    except:
        return 0.0

def highlight_dif(val):
    try:
        num = float(str(val).replace("R$", "").replace("US$", "").replace(".", "").replace(",", ".").strip())
        color = "green" if num > 0 else ("red" if num < 0 else "black")
    except:
        color = "black"
    return f"color: {color};"

# -------------------------
# Manual (CSV) -> pesos por carteira/cenário
# -------------------------
IGNORAR_NOS = {"Multimercados", "Alternativos"}  # pedido por você [file:2]

def _pct_to_float(x: str) -> float:
    s = str(x).strip().replace('"', '')
    if s == "" or s.lower() == "nan":
        return 0.0
    s = s.replace("%", "").replace(".", "").replace(",", ".")
    try:
        return float(s) / 100.0
    except:
        return 0.0

@st.cache_data
def parse_manual_csv(path_csv: str):
    """
    Retorna: pesos[carteira][cenario][no] = peso (0-1)
    Cenários: Min / Neutro / Max
    """
    df = pd.read_csv(path_csv, header=None, dtype=str, keep_default_na=False)
    pesos = {}
    col = 0
    ncols = df.shape[1]

    while col < ncols:
        header = str(df.iloc[0, col]).strip().replace('"', '')
        if header == "":
            col += 1
            continue

        if col + 3 >= ncols:
            break

        h1 = str(df.iloc[0, col+1]).strip().lower()
        h2 = str(df.iloc[0, col+2]).strip().lower()
        h3 = str(df.iloc[0, col+3]).strip().lower()

        if not (("mín" in h1 or "min" in h1) and ("neut" in h2) and ("máx" in h3 or "max" in h3)):
            col += 1
            continue

        nome = header
        pesos.setdefault(nome, {"Min": {}, "Neutro": {}, "Max": {}})

        for r in range(1, len(df)):
            no = str(df.iloc[r, col]).strip().replace('"', '')
            if no == "" or no in IGNORAR_NOS:
                continue

            pesos[nome]["Min"][no] = _pct_to_float(df.iloc[r, col+1])
            pesos[nome]["Neutro"][no] = _pct_to_float(df.iloc[r, col+2])
            pesos[nome]["Max"][no] = _pct_to_float(df.iloc[r, col+3])

        col += 4

    # Remove carteiras vazias
    pesos = {k: v for k, v in pesos.items() if len(v["Neutro"]) > 0}
    return pesos

# -------------------------
# Buckets finais RF (Brasil) e Internacional RF
# -------------------------
RF_BR_BUCKETS = [
    ("RF Pós", "Fundos de Invest."),
    ("RF Pós", "Imediato"),
    ("RF Pós", "1 a 30 dias"),
    ("RF Pós", "31 a 180 dias"),
    ("RF Pós", "181 a 360 dias"),
    ("RF Pós", "361+ dias"),
    ("RF Pós", "FiInfra e Cetipados"),
    ("RF Pré", "Bancário"),
    ("RF Pré", "Tesouro"),
    ("RF Inflação", "Bancário"),
    ("RF Inflação", "Tesouro"),
    ("RF Inflação", "FiInfra e Cetipado"),
    ("RF Inflação", "Crédito Privado"),
]

def rf_buckets_ideal(valor_total_brl: float, pesos: dict):
    out = {}
    for pai, filho in RF_BR_BUCKETS:
        w = float(pesos.get(filho, 0.0))  # no manual, subitens aparecem como % do total [file:2]
        out[f"{pai} > {filho}"] = valor_total_brl * w
    return out

def macro_weights_from_manual(pesos: dict):
    """
    Retorna pesos macro (0-1) para:
      RF_BR, RV_BR, INT_TOTAL, INT_RF, INT_RV
    Usando as linhas do manual: 'RV Brasil', 'Internacional ', 'Renda Fixa', 'Renda Variável' [file:2]
    """
    rv_br = float(pesos.get("RV Brasil", 0.0))
    intl_total = float(pesos.get("Internacional ", 0.0))  # repare o espaço no CSV [file:2]
    # Dentro de internacional:
    intl_rf = float(pesos.get("Renda Fixa", 0.0))
    intl_rv = float(pesos.get("Renda Variável", 0.0))
    # RF BR é o resto (ignorando multimercados/alternativos)
    rf_br = max(0.0, 1.0 - rv_br - intl_total)
    return rf_br, rv_br, intl_total, intl_rf, intl_rv

# -------------------------
# RV: tickers iniciais (pesos iguais para teste)
# -------------------------
RV_BR_ACOES = ["CPLE3", "EGIE3", "AXIA3", "ITUB4", "VALE3", "ALOS3", "FLRY3", "ABEV3", "PRIO3", "WEGE3"]
RV_BR_FIIS = ["KNRI11", "XPML11", "HGLG11", "PVBI11", "HGRU11", "KNCR11", "KNIP11", "KNCA11"]

RV_INT = ["VOO", "VOOG", "VIOV"]  # Schwab / EUA

def equal_weights(tickers):
    if not tickers:
        return {}
    w = 1.0 / len(tickers)
    return {t: w for t in tickers}

# -------------------------
# RV engine (reuso do seu bloco atual, generalizado)
# -------------------------
def calcular_rv_yfinance(nome_bloco: str, valor_total: float, pesos_ticker: dict, moeda: str, add_sa_suffix: bool):
    """
    Mostra Ideal x Atual por ticker usando yfinance e exporta basket.
    moeda: 'BRL' ou 'USD' (só muda formatação)
    add_sa_suffix: True para Brasil (.SA), False para internacional (ticker puro)
    """
    if valor_total <= 0 or not pesos_ticker:
        st.info(f"{nome_bloco}: sem alocação ou sem tickers cadastrados.")
        return

    fmt = format_brl if moeda == "BRL" else format_usd

    st.markdown(f"**Valor ideal do bloco:** {fmt(valor_total)}")

    col1, col2 = st.columns([1.1, 2.2], gap="large")
    qtd_input = {}

    with col1:
        st.markdown("Preencha a **quantidade atual** de cada ativo:")
        for t in pesos_ticker.keys():
            qtd_input[t] = st.text_input(t, value=st.session_state.get(f"qtd_{nome_bloco}_{t}", ""), key=f"qtd_{nome_bloco}_{t}")

    # Preços
    ativos = list(pesos_ticker.keys())
    precos = []
    ativos_ok = []
    pesos_ok = []
    qt_atual_ok = []

    for t in ativos:
        try:
            yf_ticker = f"{t}.SA" if add_sa_suffix else t
            ticker = yf.Ticker(yf_ticker)
            hist = ticker.history(period="1d")
            preco = hist["Close"].iloc[-1] if not hist.empty else None
            if preco is None or pd.isna(preco) or float(preco) <= 0:
                continue
            precos.append(float(preco))
            ativos_ok.append(t)
            pesos_ok.append(float(pesos_ticker[t]))
            qt_atual_ok.append(safe_int(qtd_input.get(t, 0)))
        except:
            continue

    if not ativos_ok:
        st.error("Nenhum ativo com preço válido foi encontrado no yfinance.")
        return

    precos = np.array(precos, dtype=float)
    pesos_ok = np.array(pesos_ok, dtype=float)
    pesos_ok = pesos_ok / pesos_ok.sum()  # normaliza por segurança
    valor_ideal = valor_total * pesos_ok

    qt_ideal = np.nan_to_num(valor_ideal / precos, nan=0, posinf=0, neginf=0).astype(int)
    qt_atual = np.array(qt_atual_ok, dtype=int)
    delta = qt_ideal - qt_atual
    oper = np.where(delta > 0, "C", np.where(delta < 0, "V", "-"))
    qtop = np.abs(delta)

    df = pd.DataFrame({
        "Ativo": ativos_ok,
        "Preço": [fmt(p) if moeda == "BRL" else f"{p:.2f}" for p in precos],
        "Peso": pesos_ok,
        "Qtd Ideal": qt_ideal,
        "Qtd Atual": qt_atual,
        "Diferença": delta,
        "Valor Ideal": [fmt(v) for v in valor_ideal],
        "Valor Atual": [fmt(p*q) for p, q in zip(precos, qt_atual)],
    })

    with col2:
        st.dataframe(df.style.applymap(highlight_dif, subset=["Diferença"]), use_container_width=True, height=550)

        # Custo
        valor_ideal_total = float(np.sum(precos * qt_ideal))
        valor_atual_total = float(np.sum(precos * qt_atual))
        custo = valor_ideal_total - valor_atual_total

        if moeda == "BRL":
            st.markdown(f"**Impacto financeiro estimado:** {format_brl(custo)}")
        else:
            st.markdown(f"**Impacto financeiro estimado:** {format_usd(custo)}")

        # Basket export
        df_export = pd.DataFrame({
            "Ativo": ativos_ok,
            "C/V": oper,
            "Quantidade": qtop,
            "Preço": precos,
        })
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df_export.to_excel(writer, sheet_name="Basket", index=False)
        st.download_button(
            label=f"Baixar Basket ({nome_bloco}).xlsx",
            data=output.getvalue(),
            file_name=f"basket_{nome_bloco}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

# -------------------------
# UI
# -------------------------
st.markdown("## Asset Allocation (Novo)")

manual_path = st.sidebar.text_input("Caminho do Manual (CSV)", value="Manual-de-Alocacao-Asset-Allocation-Novo.csv")
pesos_manual = parse_manual_csv(manual_path)

if not pesos_manual:
    st.error("Não foi possível ler o CSV ou não foram encontrados blocos de carteiras.")
    st.stop()

carteira = st.sidebar.selectbox("Carteira (manual)", list(pesos_manual.keys()))
cenario = st.sidebar.selectbox("Cenário", ["Neutro", "Min", "Max"], index=0)

patrimonio_brl = parse_input_money(st.sidebar.text_input("Patrimônio total (R$)", value="R$ 0,00"))
usdbrl = parse_input_money(st.sidebar.text_input("USD/BRL (para conversão)", value="5,00"))

fora_estrategia = parse_input_money(st.sidebar.text_input("Fora da estratégia (R$)", value="R$ 0,00"))
liquidez = parse_input_money(st.sidebar.text_input("Liquidez (R$)", value="R$ 0,00"))

alocavel_brl = max(0.0, patrimonio_brl - fora_estrategia - liquidez)

pesos = pesos_manual[carteira][cenario]

rf_br_w, rv_br_w, intl_total_w, intl_rf_w, intl_rv_w = macro_weights_from_manual(pesos)

# Valores macro (Brasil em R$; Internacional em US$ convertido)
valor_rv_br_brl = alocavel_brl * rv_br_w
valor_int_total_brl = alocavel_brl * intl_total_w
valor_int_total_usd = (valor_int_total_brl / usdbrl) if usdbrl > 0 else 0.0

valor_int_rf_usd = valor_int_total_usd * (intl_rf_w / max(1e-9, (intl_rf_w + intl_rv_w))) if (intl_rf_w + intl_rv_w) > 0 else 0.0
valor_int_rv_usd = valor_int_total_usd - valor_int_rf_usd

valor_rf_br_brl = alocavel_brl - valor_rv_br_brl - valor_int_total_brl

st.markdown(f"**Patrimônio alocável (R$):** {format_brl(alocavel_brl)}")

# -------------------------
# Macro 1: RF Brasil (R$)
# -------------------------
with st.expander("1) Renda Fixa (Brasil) — R$", expanded=True):
    st.markdown(f"**Macro RF Brasil (estimado):** {format_brl(valor_rf_br_brl)}")
    ideal = rf_buckets_ideal(alocavel_brl, pesos)  # usa buckets do manual direto [file:2]

    col_in, col_out = st.columns([1.2, 2.0], gap="large")
    rf_atual = {}

    with col_in:
        st.markdown("Preencha o **valor atual** por bucket (R$):")
        for bucket in ideal.keys():
            rf_atual[bucket] = parse_input_money(st.text_input(bucket, value=st.session_state.get(f"rf_{bucket}", ""), key=f"rf_{bucket}"))

    rows = []
    for bucket, v_ideal in ideal.items():
        v_atual = float(rf_atual.get(bucket, 0.0))
        rows.append([bucket, v_ideal, v_atual, v_ideal - v_atual])

    df_rf = pd.DataFrame(rows, columns=["Bucket", "Ideal (R$)", "Atual (R$)", "Comprar/Vender (R$)"])
    df_rf["Ideal (R$)"] = df_rf["Ideal (R$)"].apply(format_brl)
    df_rf["Atual (R$)"] = df_rf["Atual (R$)"].apply(format_brl)
    df_rf["Comprar/Vender (R$)"] = df_rf["Comprar/Vender (R$)"].apply(format_brl)

    with col_out:
        st.dataframe(df_rf.style.applymap(highlight_dif, subset=["Comprar/Vender (R$)"]), use_container_width=True, height=520)

# -------------------------
# Macro 2: RV Brasil (R$)
# -------------------------
with st.expander("2) Renda Variável (Brasil) — R$", expanded=True):
    st.markdown(f"**Macro RV Brasil (manual):** {format_brl(valor_rv_br_brl)}")

    tab1, tab2 = st.tabs(["Ações", "FIIs"])

    with tab1:
        pesos_acoes = equal_weights(RV_BR_ACOES)
        calcular_rv_yfinance("rvbr_acoes", valor_rv_br_brl, pesos_acoes, moeda="BRL", add_sa_suffix=True)

    with tab2:
        pesos_fiis = equal_weights(RV_BR_FIIS)
        calcular_rv_yfinance("rvbr_fiis", valor_rv_br_brl, pesos_fiis, moeda="BRL", add_sa_suffix=True)

# -------------------------
# Macro 3: Internacional (US$)
# -------------------------
with st.expander("3) Internacional — US$", expanded=True):
    st.markdown(f"**Macro Internacional (manual):** {format_usd(valor_int_total_usd)}  (≈ {format_brl(valor_int_total_brl)})")

    colA, colB = st.columns([1, 1], gap="large")
    with colA:
        st.markdown(f"**Internacional RF (manual):** {format_usd(valor_int_rf_usd)}")
        # Por enquanto: só consolidado, pois o manual não detalha buckets de RF internacional além do macro [file:2]
        st.info("RF Internacional: hoje está consolidada no manual como 'Renda Fixa'. Podemos detalhar em buckets depois.")

    with colB:
        st.markdown(f"**Internacional RV (manual):** {format_usd(valor_int_rv_usd)}")
        pesos_int = equal_weights(RV_INT)
        calcular_rv_yfinance("int_rv", valor_int_rv_usd, pesos_int, moeda="USD", add_sa_suffix=False)
