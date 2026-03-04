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
tab_up, tab_aa = st.tabs(["Atualizar posições", "Asset Allocation"])

with tab_up:
    st.subheader("Posições (lidas do GitHub)")
    st.caption("O app lê os arquivos na pasta ./posicoes. Para atualizar, faça commit de novos arquivos com o mesmo nome.")

    st.write("Arquivos esperados:")
    st.code(
        "posicoes/Contas.xlsx\n"
        "posicoes/XP.xlsx\n"
        "posicoes/BTG.xlsx\n"
        "posicoes/CSProdutos.csv"
    )

    from pathlib import Path
    base = Path("posicoes")
    paths = {
        "Contas.xlsx": base / "Contas.xlsx",
        "XP.xlsx": base / "XP.xlsx",
        "BTG.xlsx": base / "BTG.xlsx",
        "CSProdutos.csv": base / "CSProdutos.csv",
    }
    for name, p in paths.items():
        st.write(f"{'OK' if p.exists() else 'FALTA'} - {name}")
                 
    col1, col2 = st.columns([1, 1])
    with col1:
        dt_pos = st.date_input("Data da posição", value=datetime.now().date())
    with col2:
        rebuild = st.button("Rebuild latest agora", type="primary")

    if rebuild:
        try:
            df_latest = posmod.build_latest_from_repo(dt_posicao=dt_pos.isoformat())
            st.success(f"Rebuild OK! Linhas: {len(df_latest):,}")
            def show_diagnostics_from_meta():
                meta_path = Path("data") / "positions_meta.json"
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    except Exception as e:
                        st.error(f"Não consegui ler positions_meta.json: {type(e).__name__} - {e}")
                        return
            
                    diag = meta.get("diagnostics", {})
                    if not diag:
                        st.warning("positions_meta.json existe, mas não tem campo diagnostics.")
                        st.json(meta)
                        return
            
                    st.markdown("### Diagnóstico por corretora (parser)")
                    st.json(diag)
            
                    # Mostra só a parte mais útil (unmatched)
                    def pick_unmatched(d):
                        return {
                            "unmatched_rows": d.get("unmatched_rows"),
                            "unmatched_by_broker": d.get("unmatched_by_broker"),
                            "unmatched_examples": d.get("unmatched_examples"),
                        }
            
                    st.markdown("### Contas que não casaram (resumo)")
                    st.json({
                        "xp_df": pick_unmatched(diag.get("xp_df", {})),
                        "btg_df": pick_unmatched(diag.get("btg_df", {})),
                        "cs_df": pick_unmatched(diag.get("cs_df", {})),
                    })
                else:
                    st.info("Ainda não existe data/positions_meta.json (faça Rebuild).")
            
            def show_quick_counts(df_latest):
                st.markdown("### Resumo rápido (latest)")
                if "corretora" in df_latest.columns:
                    st.write("Linhas por corretora:")
                    st.dataframe(df_latest["corretora"].value_counts().rename("linhas"))
                if {"corretora","conta"}.issubset(df_latest.columns):
                    # linhas sem match no controle (CLIENTE vazio)
                    if "CLIENTE" in df_latest.columns:
                        miss = df_latest["CLIENTE"].isna() | (df_latest["CLIENTE"].astype(str).str.strip() == "")
                        st.write(f"Linhas sem match no Contas.xlsx (CLIENTE vazio): {int(miss.sum())}")
                        if "corretora" in df_latest.columns:
                            st.write("Sem match por corretora:")
                            st.dataframe(df_latest.loc[miss, "corretora"].value_counts().rename("linhas"))

            st.dataframe(df_latest.head(50), use_container_width=True, height=420)
        except Exception as e:
            st.error(f"Erro no rebuild: {type(e).__name__} - {e}")

    # opcional: mostrar o que já está salvo
    df_cached = posmod.load_latest_positions()
    if df_cached is not None:
        st.markdown("Último latest salvo (cache parquet):")
        st.dataframe(df_cached.head(30), use_container_width=True, height=360)

with tab_aa:
    # =========================
    # TODO: aqui ainda está seu AA original (mantenha/cole sua lógica)
    # =========================

    # 1) Carregar pesos
    try:
        pesos_manual = load_pesos_xlsx("Pesos-alocacao.xlsx")
    except Exception as e:
        st.error(f"Erro ao ler Pesos-alocacao.xlsx: {type(e).__name__} - {e}")
        st.stop()

    carteiras = sorted(list(pesos_manual.keys()))

    # 2) Sidebar (mantive seu comportamento atual)
    with st.sidebar:
        st.markdown("### Parâmetros da carteira")
        carteira = st.selectbox("Carteira", carteiras)
        patrimonio_brl = parse_input_money(st.text_input("Patrimônio total (R$)", value="500000"))

        try:
            ptax, data_ptax = get_ptax_usdbrl_last()
            usdbrl = ptax
            st.caption(f"PTAX venda automática: {usdbrl:.4f} ({data_ptax})")
            usar_manual = st.checkbox("Editar USDBRL manualmente", value=False)
        except Exception:
            usdbrl = 5.00
            usar_manual = True
            st.warning("Falha ao buscar PTAX. Usando USDBRL manual.")

        if usar_manual:
            usdbrl = parse_input_money(st.text_input("USDBRL", value=str(usdbrl).replace(".", ",")))

    # 3) Cálculo ideal (mantive seu motor)
    p = pesos_manual[carteira]
    rf_br_w, rv_br_w, intl_w, intl_rf_w, intl_rv_w = macro_weights_from_neutro(p)

    alocavel_brl = max(0.0, patrimonio_brl)
    valor_rv_total_brl = alocavel_brl * rv_br_w
    valor_int_total_brl = alocavel_brl * intl_w
    valor_int_total_usd = (valor_int_total_brl / usdbrl) if usdbrl else 0.0
    valor_rf_br_brl = max(0.0, alocavel_brl - valor_rv_total_brl - valor_int_total_brl)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Patrimônio (R$)", format_brl(alocavel_brl))
    c2.metric("RF Brasil", format_brl(valor_rf_br_brl), delta=fmt_pct(rf_br_w))
    c3.metric("RV Brasil", format_brl(valor_rv_total_brl), delta=fmt_pct(rv_br_w))
    c4.metric("Internacional", format_brl(valor_int_total_brl), delta=fmt_pct(intl_w))

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
