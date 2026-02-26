import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
from datetime import datetime, timedelta
import requests

# Import defensivo do yfinance
try:
    import yfinance as yf
    HAS_YF = True
except Exception:
    HAS_YF = False

st.set_page_config(page_title="Asset Allocation (Novo)", layout="wide")

# -------------------------
# Utils
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

def highlight_dif(val):
    try:
        num = float(str(val).replace("R$", "").replace("US$", "").replace(".", "").replace(",", ".").strip())
        color = "green" if num > 0 else ("red" if num < 0 else "black")
    except:
        color = "black"
    return f"color: {color};"

# -------------------------
# Manual CSV (robusto)
# -------------------------
IGNORAR_NOS = {"Multimercados", "Alternativos"}

def _pct_to_float(x: str) -> float:
    s = str(x).strip().replace('"', '')
    if s == "" or s.lower() == "nan":
        return 0.0
    s = s.replace("%", "").replace(".", "").replace(",", ".")
    try:
        return float(s) / 100.0
    except:
        return 0.0

def _read_csv_robusto(file_or_path):
    # file_or_path pode ser path (str) ou arquivo do uploader (BytesIO)
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin1"):
        try:
            return pd.read_csv(
                file_or_path,
                header=None,
                dtype=str,
                keep_default_na=False,
                encoding=enc,
            )
        except UnicodeDecodeError:
            continue
    return pd.read_csv(
        file_or_path,
        header=None,
        dtype=str,
        keep_default_na=False,
        engine="python",
        encoding="latin1",
    )

@st.cache_data
def parse_manual_csv_from_df(df: pd.DataFrame):
    """
    Retorna: pesos[carteira]["Neutro"][no] = peso (0-1)
    Só Neutro.
    """
    pesos = {}
    ncols = df.shape[1]

    def is_min(s): 
        s = str(s).strip().lower()
        return ("mín" in s) or ("min" in s)

    def is_neutro(s): 
        s = str(s).strip().lower()
        return "neut" in s

    def is_max(s):
        s = str(s).strip().lower()
        return ("máx" in s) or ("max" in s)

    # varre coluna a coluna e identifica blocos [Carteira, Min, Neutro, Max]
    for col in range(0, ncols - 3):
        header = str(df.iloc[0, col]).strip().replace('"', '')
        if header == "":
            continue

        h1 = df.iloc[0, col + 1]
        h2 = df.iloc[0, col + 2]
        h3 = df.iloc[0, col + 3]

        if not (is_min(h1) and is_neutro(h2) and is_max(h3)):
            continue

        nome = header
        pesos.setdefault(nome, {"Neutro": {}})

        for r in range(1, len(df)):
            no = str(df.iloc[r, col]).strip().replace('"', '')
            if no == "" or no in IGNORAR_NOS:
                continue

            pesos[nome]["Neutro"][no] = _pct_to_float(df.iloc[r, col + 2])  # só Neutro

    # remove vazias
    pesos = {k: v for k, v in pesos.items() if len(v["Neutro"]) > 0}
    return pesos

def load_manual_weights():
    """
    Tenta:
      1) arquivo na raiz do projeto
      2) upload pelo usuário
    """
    default_name = "Manual-de-Alocacao-Asset-Allocation-Novo.csv"

    # tenta ler do repo (raiz)
    try:
        df = _read_csv_robusto(default_name)
        pesos = parse_manual_csv_from_df(df)
        if pesos:
            return pesos, default_name, None
    except Exception:
        pass

    # fallback: uploader
    up = st.sidebar.file_uploader("Subir Manual (CSV)", type=["csv"])
    if up is None:
        return {}, default_name, "Envie o CSV do Manual no uploader (ou deixe o arquivo na raiz do repo)."

    df = _read_csv_robusto(up)
    pesos = parse_manual_csv_from_df(df)
    return pesos, up.name, None

# -------------------------
# PTAX (BCB) - última cotação disponível (venda)
# -------------------------
@st.cache_data(ttl=3600)
def get_ptax_usdbrl_last():
    """
    Busca PTAX USD/BRL (cotacaoVenda) na API Olinda do BCB.
    Tenta últimos 10 dias para pegar o último dia útil.
    """
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

# -------------------------
# Buckets RF Brasil
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

def rf_buckets_ideal(valor_total_brl: float, pesos_neutro: dict):
    out = {}
    for pai, filho in RF_BR_BUCKETS:
        w = float(pesos_neutro.get(filho, 0.0))
        out[f"{pai} > {filho}"] = valor_total_brl * w
    return out

def macro_weights_from_manual_neutro(pesos_neutro: dict):
    rv_br = float(pesos_neutro.get("RV Brasil", 0.0))
    intl_total = float(pesos_neutro.get("Internacional ", 0.0))  # tem espaço no CSV
    intl_rf = float(pesos_neutro.get("Renda Fixa", 0.0))
    intl_rv = float(pesos_neutro.get("Renda Variável", 0.0))
    rf_br = max(0.0, 1.0 - rv_br - intl_total)
    return rf_br, rv_br, intl_total, intl_rf, intl_rv

# -------------------------
# Tickers iniciais (teste)
# -------------------------
RV_BR_ACOES = ["CPLE3", "EGIE3", "AXIA3", "ITUB4", "VALE3", "ALOS3", "FLRY3", "ABEV3", "PRIO3", "WEGE3"]
RV_BR_FIIS  = ["KNRI11", "XPML11", "HGLG11", "PVBI11", "HGRU11", "KNCR11", "KNIP11", "KNCA11"]
RV_INT = ["VOO", "VOOG", "VIOV"]

def equal_weights(tickers):
    if not tickers:
        return {}
    w = 1.0 / len(tickers)
    return {t: w for t in tickers}

# -------------------------
# RV engine
# -------------------------
def calcular_rv_yfinance(nome_bloco: str, valor_total: float, pesos_ticker: dict, moeda: str, add_sa_suffix: bool):
    if valor_total <= 0 or not pesos_ticker:
        st.info(f"{nome_bloco}: sem alocação ou sem tickers cadastrados.")
        return

    if not HAS_YF:
        st.error("yfinance não está disponível. Verifique requirements.txt e reinicie o app.")
        return

    fmt = format_brl if moeda == "BRL" else format_usd
    st.markdown(f"**Valor ideal do bloco:** {fmt(valor_total)}")

    col1, col2 = st.columns([1.1, 2.2], gap="large")
    qtd_input = {}

    with col1:
        st.markdown("Preencha a **quantidade atual** de cada ativo:")
        for t in pesos_ticker.keys():
            qtd_input[t] = st.text_input(
                t,
                value=st.session_state.get(f"qtd_{nome_bloco}_{t}", ""),
                key=f"qtd_{nome_bloco}_{t}"
            )

    ativos = list(pesos_ticker.keys())
    precos, ativos_ok, pesos_ok, qt_atual_ok = [], [], [], []

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
    oper = np.where(delta > 0, "C", np.where(delta < 0, "V", "-"))
    qtop = np.abs(delta)

    df = pd.DataFrame({
        "Ativo": ativos_ok,
        "Preço": [format_brl(p) if moeda == "BRL" else f"{p:.2f}" for p in precos],
        "Peso": pesos_ok,
        "Qtd Ideal": qt_ideal,
        "Qtd Atual": qt_atual,
        "Diferença": delta,
        "Valor Ideal": [fmt(v) for v in valor_ideal],
        "Valor Atual": [fmt(p*q) for p, q in zip(precos, qt_atual)],
    })

    with col2:
        st.dataframe(df.style.applymap(highlight_dif, subset=["Diferença"]), use_container_width=True, height=550)

        valor_ideal_total = float(np.sum(precos * qt_ideal))
        valor_atual_total = float(np.sum(precos * qt_atual))
        custo = valor_ideal_total - valor_atual_total

        st.markdown(f"**Impacto financeiro estimado:** {fmt(custo)}")

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

pesos_manual, manual_fonte, manual_erro = load_manual_weights()
if manual_erro:
    st.sidebar.warning(manual_erro)

if not pesos_manual:
    st.error("Não foi possível carregar o Manual. Suba o CSV no uploader ou deixe o arquivo na raiz do repositório.")
    st.stop()

st.sidebar.caption(f"Manual carregado: {manual_fonte}")
carteira = st.sidebar.selectbox("Carteira (manual)", list(pesos_manual.keys()))

patrimonio_brl = parse_input_money(st.sidebar.text_input("Patrimônio total (R$)", value="5000000"))

# PTAX automático + fallback manual
try:
    ptax, data_ptax = get_ptax_usdbrl_last()
    usdbrl = ptax
    st.sidebar.caption(f"PTAX (venda) automática: {usdbrl:.4f} ({data_ptax})")
    usar_manual = st.sidebar.checkbox("Editar USD/BRL manualmente", value=False)
except Exception:
    usdbrl = 5.00
    usar_manual = True
    st.sidebar.warning("Não consegui buscar PTAX automaticamente. Usando fallback manual.")

if usar_manual:
    usdbrl = parse_input_money(st.sidebar.text_input("USD/BRL", value=str(usdbrl).replace(".", ",")))

alocavel_brl = max(0.0, patrimonio_brl)

pesos_neutro = pesos_manual[carteira]["Neutro"]
rf_br_w, rv_br_w, intl_total_w, intl_rf_w, intl_rv_w = macro_weights_from_manual_neutro(pesos_neutro)

valor_rv_br_brl = alocavel_brl * rv_br_w
valor_int_total_brl = alocavel_brl * intl_total_w
valor_int_total_usd = (valor_int_total_brl / usdbrl) if usdbrl > 0 else 0.0

den = (intl_rf_w + intl_rv_w)
valor_int_rf_usd = valor_int_total_usd * (intl_rf_w / den) if den > 0 else 0.0
valor_int_rv_usd = max(0.0, valor_int_total_usd - valor_int_rf_usd)

valor_rf_br_brl = max(0.0, alocavel_brl - valor_rv_br_brl - valor_int_total_brl)

st.markdown(f"**Patrimônio (R$):** {format_brl(alocavel_brl)}")

# -------------------------
# 1) RF Brasil
# -------------------------
with st.expander("1) Renda Fixa (Brasil) — R$", expanded=True):
    st.markdown(f"**Macro RF Brasil (estimado):** {format_brl(valor_rf_br_brl)}")

    ideal = rf_buckets_ideal(alocavel_brl, pesos_neutro)

    col_in, col_out = st.columns([1.2, 2.0], gap="large")
    rf_atual = {}

    with col_in:
        st.markdown("Preencha o **valor atual** por bucket (R$):")
        for bucket in ideal.keys():
            rf_atual[bucket] = parse_input_money(
                st.text_input(bucket, value=st.session_state.get(f"rf_{bucket}", ""), key=f"rf_{bucket}")
            )

    rows = []
    for bucket, v_ideal in ideal.items():
        v_atual = float(rf_atual.get(bucket, 0.0))
        rows.append([bucket, v_ideal, v_atual, v_ideal - v_atual])

    df_rf = pd.DataFrame(rows, columns=["Bucket", "Ideal", "Atual", "Comprar/Vender"])
    df_rf["Ideal"] = df_rf["Ideal"].apply(format_brl)
    df_rf["Atual"] = df_rf["Atual"].apply(format_brl)
    df_rf["Comprar/Vender"] = df_rf["Comprar/Vender"].apply(format_brl)

    with col_out:
        st.dataframe(df_rf.style.applymap(highlight_dif, subset=["Comprar/Vender"]), use_container_width=True, height=520)

# -------------------------
# 2) RV Brasil
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
# 3) Internacional
# -------------------------
with st.expander("3) Internacional — US$", expanded=True):
    st.markdown(f"**Macro Internacional (manual):** {format_usd(valor_int_total_usd)}  (≈ {format_brl(valor_int_total_brl)})")

    colA, colB = st.columns([1, 1], gap="large")

    with colA:
        st.markdown(f"**Internacional RF (manual):** {format_usd(valor_int_rf_usd)}")
        st.info("RF Internacional está consolidada como 'Renda Fixa' no manual. Se você definir buckets (ex.: Treasuries, IG, HY, etc.), eu abro igual RF Brasil.")

    with colB:
        st.markdown(f"**Internacional RV (manual):** {format_usd(valor_int_rv_usd)}")
        pesos_int = equal_weights(RV_INT)
        calcular_rv_yfinance("int_rv", valor_int_rv_usd, pesos_int, moeda="USD", add_sa_suffix=False)
