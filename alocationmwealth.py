from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from datetime import datetime, timedelta
import unicodedata

import numpy as np
import pandas as pd
import requests
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go

import positions as posmod

try:
    import yfinance as yf
    HAS_YF = True
except Exception:
    HAS_YF = False

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak
    HAS_REPORTLAB = True
except Exception:
    HAS_REPORTLAB = False

# =============================================================================
# CONFIGURAÇÃO GERAL
# =============================================================================
st.set_page_config(page_title="M Wealth | Asset Allocation", layout="wide", page_icon="📊")

BASE_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
DATA_DIR = BASE_DIR / "data"
POS_DIR = BASE_DIR / "posicoes"
LOGO_CANDIDATES = [BASE_DIR / "Logo-M-Wealth.png", POS_DIR / "Logo-M-Wealth.png"]

# Carteira estratégica de RV no código, conforme orientação: consultor visualiza, não edita.
ACOES_SEM_RENDA = ["AXIA3", "EQTL3", "SBSP3", "ITUB3", "BPAC11", "PSSA3", "PRIO3", "VALE3", "WEGE3", "RENT3"]
ACOES_COM_RENDA = ["CPLE3", "EGIE3", "AXIA3", "ITUB3", "VALE3", "ALOS3", "FLRY3", "ABEV3", "PRIO3", "WEGE3"]
FIIS_RECOMENDADOS = ["KNRI11", "XPML11", "HGLG11", "PVBI11", "HGRU11", "KNCR11", "KNIP11", "KNCA11"]
FI_INFRA_TICKERS = ["KNDI11", "CDII11", "IFRI11", "AZQI11", "KNCE11", "AZIN11", "JURO11", "IFRA11", "KDIF11", "JGPI11", "BDIF11", "JMBI11", "CPTI11"]

# Ordem institucional de exibição dos subbuckets.
SUBBUCKET_ORDER = [
    "Pós - Imediato",
    "Pós - 1 a 30 dias",
    "Pós - 31 a 180 dias",
    "Pós - 181 a 360 dias",
    "Pós - 361+ dias",
    "FiInfra e Cetipados",
    "Pré - Bancário",
    "Pré - Tesouro",
    "Inflação - Bancário",
    "Inflação - Tesouro",
    "Inflação - FiInfra e Cetipado",
    "Crédito Privado",
    "Ações",
    "FIIs",
    "Renda Fixa Internacional",
    "Renda Variável Internacional",
    "Saldo em Conta",
    "Fundos de Investimento / Sem Liquidez Mapeada",
    "COE / Estruturados",
    "Previdência",
    "Outros / Não Classificado",
]

st.markdown(
    """
    <style>
    .block-container { padding-top: 1.1rem; padding-bottom: 2rem; }
    div[data-testid="stMetricValue"] { font-size: 1.32rem; }
    div[data-testid="stMetricDelta"] { font-size: 0.82rem; }
    .mw-card {
        border: 1px solid rgba(255,255,255,0.08);
        background: rgba(255,255,255,0.035);
        padding: 0.85rem 1rem;
        border-radius: 14px;
        margin-bottom: 0.5rem;
    }
    .mw-subtle { color: rgba(250,250,250,0.68); font-size: 0.88rem; }
    .mw-divider { border-top: 1px solid rgba(255,255,255,0.08); margin: 0.8rem 0 1.0rem 0; }
    .stDataFrame { font-size: 0.88rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# =============================================================================
# UTILITÁRIOS
# =============================================================================
def find_file(filename: str) -> Path:
    for p in [POS_DIR / filename, BASE_DIR / filename, Path(filename)]:
        if p.exists():
            return p
    return POS_DIR / filename


def logo_path() -> Path | None:
    for p in LOGO_CANDIDATES:
        if p.exists():
            return p
    return None


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(s)) if not unicodedata.combining(c))


def norm(s) -> str:
    return strip_accents(str(s or "")).upper().strip()


def clean_ticker(s) -> str:
    return norm(str(s)).replace(" ", "")[:20]


def format_brl(v) -> str:
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def format_usd(v) -> str:
    try:
        return f"US$ {float(v):,.2f}"
    except Exception:
        return "US$ 0.00"


def fmt_pct(x) -> str:
    try:
        return f"{100 * float(x):.2f}%".replace(".", ",")
    except Exception:
        return "0,00%"


def parse_num(x) -> float:
    try:
        if pd.isna(x):
            return 0.0
        if isinstance(x, (int, float, np.number)):
            return float(x)
        s = str(x).replace("R$", "").replace("US$", "").strip()
        # formato brasileiro exibido
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        return float(s)
    except Exception:
        return 0.0


def style_diff(val):
    num = parse_num(val)
    if num > 1:
        return "color: #35c46a; font-weight: 700;"
    if num < -1:
        return "color: #ff5c5c; font-weight: 700;"
    return "color: rgba(255,255,255,0.70);"


def style_status(val):
    s = norm(val)
    if "COMPRAR" in s or "APORTAR" in s or "FALTA" in s:
        return "color: #35c46a; font-weight: 700;"
    if "VENDER" in s or "REDUZIR" in s or "EXCESSO" in s:
        return "color: #ff5c5c; font-weight: 700;"
    if "OK" in s or "MANTER" in s:
        return "color: #f0c36a; font-weight: 700;"
    return ""


def acao_por_diff(diff: float, tolerancia: float = 100.0) -> str:
    if diff > tolerancia:
        return "Comprar / Aportar"
    if diff < -tolerancia:
        return "Vender / Reduzir"
    return "Manter / OK"


def safe_cols(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    clean = df.loc[:, ~df.columns.duplicated()].copy()
    return clean[[c for c in cols if c in clean.columns]].copy()


@st.cache_data(ttl=3600)
def get_ptax_usdbrl_last():
    base = "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoDolarPeriodo"
    hoje = datetime.now().date()
    ini = hoje - timedelta(days=10)
    url = (
        f"{base}(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
        f"?@dataInicial='{ini.strftime('%m-%d-%Y')}'&@dataFinalCotacao='{hoje.strftime('%m-%d-%Y')}'"
        f"&$format=json&$select=cotacaoVenda,dataHoraCotacao&$orderby=dataHoraCotacao desc&$top=1"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    val = r.json().get("value", [])
    if not val:
        raise ValueError("Sem PTAX no período consultado.")
    return float(val[0]["cotacaoVenda"]), val[0]["dataHoraCotacao"]


@st.cache_data(ttl=3600)
def load_contas() -> pd.DataFrame:
    try:
        path = find_file("Contas.xlsx")
        df = pd.read_excel(path, sheet_name=0)
        df.columns = [str(c).strip() for c in df.columns]
        return df
    except Exception as e:
        st.warning(f"Não foi possível carregar Contas.xlsx: {e}")
        return pd.DataFrame()


@st.cache_data
def load_pesos_xlsx(path_xlsx: str = "Pesos-alocacao.xlsx") -> dict[str, dict[str, float]]:
    path = find_file(path_xlsx)
    xls = pd.ExcelFile(path, engine="openpyxl")
    df = pd.read_excel(xls, sheet_name=xls.sheet_names[0], header=None).fillna("")
    pesos: dict[str, dict[str, float]] = {}
    carteira_atual = None

    for _, row in df.iterrows():
        a = str(row.iloc[0]).strip()
        b = str(row.iloc[1]).strip()
        if a == "" and b == "":
            continue
        if b.lower() == "neutro" and a:
            carteira_atual = a
            pesos.setdefault(carteira_atual, {})
            continue
        if not carteira_atual or not a:
            continue
        try:
            w = float(str(row.iloc[1]).replace(",", ".").strip())
        except Exception:
            w = 0.0
        pesos[carteira_atual][a] = w
    return {k: v for k, v in pesos.items() if v}


def peso_get(p: dict[str, float], key: str) -> float:
    nk = norm(key)
    for k, v in p.items():
        if norm(k) == nk:
            return float(v)
    return 0.0


def macro_targets_from_model(p: dict[str, float], pl: float) -> pd.DataFrame:
    # Usa as linhas macro da planilha. É a visão correta de soma 100%.
    rf = peso_get(p, "RF Pós") + peso_get(p, "RF Pré") + peso_get(p, "RF Inflação")
    rv = peso_get(p, "RV Brasil")
    intl = peso_get(p, "Internacional")
    rows = [
        ["RF Brasil", rf, pl * rf],
        ["RV Brasil", rv, pl * rv],
        ["Internacional", intl, pl * intl],
    ]
    return pd.DataFrame(rows, columns=["Classe", "Peso Ideal", "Valor Ideal"])


def subbucket_targets_from_model(p: dict[str, float], pl: float) -> pd.DataFrame:
    # Linhas operacionais: remove os pais duplicados (RF Pós, Fundos de Invest., RF Pré, RF Inflação, RV Brasil, Internacional).
    mapping = [
        ("RF Brasil", "Pós - Imediato", "Imediato"),
        ("RF Brasil", "Pós - 1 a 30 dias", "1 a 30 dias"),
        ("RF Brasil", "Pós - 31 a 180 dias", "31 a 180 dias"),
        ("RF Brasil", "Pós - 181 a 360 dias", "181 a 360 dias"),
        ("RF Brasil", "Pós - 361+ dias", "361+ dias"),
        ("RF Brasil", "FiInfra e Cetipados", "FiInfra e Cetipados"),
        ("RF Brasil", "Pré - Bancário", "Bancário Pré"),
        ("RF Brasil", "Pré - Tesouro", "Tesouro Pré"),
        ("RF Brasil", "Inflação - Bancário", "Bancário"),
        ("RF Brasil", "Inflação - Tesouro", "Tesouro"),
        ("RF Brasil", "Inflação - FiInfra e Cetipado", "FiInfra e Cetipado"),
        ("RF Brasil", "Crédito Privado", "Crédito Privado"),
        ("RV Brasil", "Ações", "Ações"),
        ("RV Brasil", "FIIs", "FIIs"),
        ("Internacional", "Renda Fixa Internacional", "Renda Fixa"),
        ("Internacional", "Renda Variável Internacional", "Renda Variável"),
    ]
    rows = []
    for classe, bucket, raw_key in mapping:
        w = peso_get(p, raw_key)
        if abs(w) > 1e-12:
            rows.append([classe, bucket, w, pl * w])
    return pd.DataFrame(rows, columns=["Classe", "Subbucket", "Peso Ideal", "Valor Ideal"])


def model_for_profile(perfil: str, modelos: list[str]) -> str | None:
    p = norm(perfil)
    ordered = [
        ("ARROJADO RENDA CONSTRUCAO", "Arrojado Renda Construção"),
        ("MODERADO RENDA CONSTRUCAO", "Moderado Renda Construção"),
        ("CONSERVADOR RENDA CONSTRUCAO", "Conservador Renda Construção"),
        ("ARROJADO RENDA USUFRUTO", "Arrojado Renda Usufruto"),
        ("MODERADO RENDA USUFRUTO", "Moderado Renda Usufruto"),
        ("CONSERVADOR RENDA USUFRUTO", "Conservador Renda Usufruto"),
        ("ULTRACONSERVADOR", "Ultraconservador"),
        ("ARROJADO", "Arrojado"),
        ("MODERADO", "Moderado"),
        ("CONSERVADOR", "Conservador"),
    ]
    for needle, modelo in ordered:
        if needle in p and modelo in modelos:
            return modelo
    return None


def rv_universe(modelo: str) -> dict[str, list[str]]:
    # Renda Construção costuma demandar mais crescimento/retorno; Renda Usufruto prioriza renda.
    if "RENDA" in norm(modelo):
        acoes = ACOES_COM_RENDA
    else:
        acoes = ACOES_SEM_RENDA
    return {"Ações": acoes, "FIIs": FIIS_RECOMENDADOS}


def classify_position(row: pd.Series) -> pd.Series:
    corretora = norm(row.get("corretora", ""))
    asset_id = clean_ticker(row.get("asset_id", ""))
    asset_tipo = norm(row.get("asset_tipo", ""))
    mercado = norm(row.get("mercado", ""))
    subm = norm(row.get("sub_mercado", ""))
    estrat = norm(row.get("estrategia", ""))
    nome = norm(row.get("asset_nome", ""))
    text = " ".join([asset_id, nome, asset_tipo, mercado, subm, estrat])

    if corretora == "CS":
        if any(x in text for x in ["FIXED", "BOND", "TREASURY", "CORPORATE", "CD", "NOTE"]):
            return pd.Series(["Internacional", "Renda Fixa Internacional", "Renda Fixa Internacional"])
        if any(x in text for x in ["CASH", "MONEY MARKET", "SWEEP"]):
            return pd.Series(["Internacional", "Caixa Internacional", "Renda Fixa Internacional"])
        return pd.Series(["Internacional", "Renda Variável Internacional", "Renda Variável Internacional"])

    if any(x in text for x in ["SALDO", "CONTA CORRENTE", "FINANCEIRO", "CUSTODIA REMUNERADA"]):
        return pd.Series(["Caixa", "Saldo em Conta", "Saldo em Conta"])

    if any(x in text for x in ["PREVIDENCIA", "PGBL", "VGBL"]):
        return pd.Series(["Outros", "Previdência", "Previdência"])

    if any(x in text for x in ["COE", "ESTRUTURADO", "OPCOES FLEX", "OPCAO"]):
        return pd.Series(["Outros", "COE / Estruturados", "COE / Estruturados"])

    if asset_id in FI_INFRA_TICKERS or any(x in text for x in ["FI INFRA", "FIINFRA", "DEB INCENTIVADA", "INFRA"]):
        return pd.Series(["RF Brasil", "FiInfra e Cetipados", "FiInfra e Cetipados"])

    if any(x in text for x in ["FUNDO IMOB", "FII", "FUNDO IMOBILIARIO"]) or (asset_id.endswith("11") and asset_id[:4].isalpha()):
        return pd.Series(["RV Brasil", "FIIs", "FIIs"])

    if any(x in text for x in ["ACAO", "AÇÃO", "BOVESPA", "RENDA VARIAVEL"]) or (len(asset_id) in [5, 6] and asset_id[:4].isalpha() and asset_id[-1].isdigit()):
        return pd.Series(["RV Brasil", "Ações", "Ações"])

    if any(x in text for x in ["TESOURO SELIC", "LFT", "SELIC"]):
        return pd.Series(["RF Brasil", "Pós - Imediato", "Pós - Imediato"])

    if any(x in text for x in ["TESOURO PRE", "NTN-F", "NTNF", "LTN"]):
        return pd.Series(["RF Brasil", "Pré - Tesouro", "Pré - Tesouro"])

    if any(x in text for x in ["TESOURO IPCA", "NTN-B", "NTNB"]):
        return pd.Series(["RF Brasil", "Inflação - Tesouro", "Inflação - Tesouro"])

    if any(x in text for x in ["IPCA", "INFLACAO", "INFLAÇÃO"]):
        if any(x in text for x in ["CRI", "CRA", "DEB", "CDCA"]):
            return pd.Series(["RF Brasil", "Inflação - Bancário", "Inflação - Bancário"])
        return pd.Series(["RF Brasil", "Inflação - Bancário", "Inflação - Bancário"])

    if any(x in text for x in ["PRE-FIXADO", "PRE FIXADO", "PRÉ-FIXADO", "PRE", "PREFIXADO"]):
        if "TESOURO" in text:
            return pd.Series(["RF Brasil", "Pré - Tesouro", "Pré - Tesouro"])
        return pd.Series(["RF Brasil", "Pré - Bancário", "Pré - Bancário"])

    if any(x in text for x in ["CRI", "CRA", "DEBENTURE", "DEBENTURE", "CDCA", "CREDITO PRIVADO", "CRÉDITO PRIVADO"]):
        return pd.Series(["RF Brasil", "Crédito Privado", "Crédito Privado"])

    if any(x in text for x in ["CDB", "LCI", "LCA", "LCD", "COMPROMISSADA", "POS-FIXADO", "PÓS-FIXADO", "CDI"]):
        return pd.Series(["RF Brasil", "Pós - 361+ dias", "Pós - 361+ dias"])

    if any(x in text for x in ["FIC", "FIM", "FIRF", "FIDC", "FUNDO", "FUNDOS"]):
        return pd.Series(["RF Brasil", "Fundos de Investimento / Sem Liquidez Mapeada", "Fundos de Investimento / Sem Liquidez Mapeada"])

    return pd.Series(["Outros", "Outros / Não Classificado", "Outros / Não Classificado"])


def enrich_positions(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adiciona as colunas de classificação usadas pelo app.

    Importante: essa função pode ser chamada mais de uma vez durante o fluxo
    do Streamlit. Por isso, antes de recalcular, removemos classificações
    antigas e eliminamos colunas duplicadas. Sem isso, o pandas pode retornar
    um DataFrame quando acessamos df["subbucket"], gerando o erro:
    ValueError: Cannot index with multidimensional key.
    """
    df = df.copy()

    # Proteção contra colunas duplicadas vindas de concat/merge/cache/session_state
    df = df.loc[:, ~df.columns.duplicated()].copy()

    # Recalcula sempre as colunas derivadas, evitando duplicidade
    derived_cols = ["classe_macro", "subclasse", "subbucket"]
    df = df.drop(columns=[c for c in derived_cols if c in df.columns], errors="ignore")

    for c in ["valor_mercado", "quantidade"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    if "asset_id" in df.columns:
        df["asset_id"] = df["asset_id"].astype(str).str.strip()

    cols = df.apply(classify_position, axis=1)
    cols.columns = derived_cols
    df = pd.concat([df, cols], axis=1)
    return df.loc[:, ~df.columns.duplicated()].copy()


def get_latest_positions_auto(force: bool = False) -> tuple[pd.DataFrame | None, str]:
    """Carrega a última posição automaticamente e reconstrói quando os arquivos fonte são mais novos.

    Compatível com parquet e pickle. O pickle é fallback para máquinas onde pyarrow/fastparquet
    não foi instalado corretamente.
    """
    latest_parquet = DATA_DIR / "positions_latest.parquet"
    latest_pickle = DATA_DIR / "positions_latest.pkl"
    latest_candidates = [p for p in [latest_parquet, latest_pickle] if p.exists()]
    source_files = [find_file("Contas.xlsx"), find_file("XP.xlsx"), find_file("BTG.xlsx"), find_file("CSProdutos.csv")]

    try:
        if force or not latest_candidates:
            df = posmod.build_latest_from_repo()
            return df, "rebuild"

        latest_mtime = max(p.stat().st_mtime for p in latest_candidates)
        sources_newer = [p for p in source_files if p.exists() and p.stat().st_mtime > latest_mtime]
        if sources_newer:
            df = posmod.build_latest_from_repo()
            return df, "rebuild_new_sources"

        df = posmod.load_latest_positions()
        if df is None or df.empty:
            df = posmod.build_latest_from_repo()
            return df, "rebuild_empty"
        return df, "cache"
    except Exception as e:
        st.error(f"Erro ao carregar/consolidar posições: {e}")
        return None, "error"


def portfolio_actual_tables(pos_cliente: pd.DataFrame, p: dict[str, float], pl_total: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    macro_target = macro_targets_from_model(p, pl_total)
    sub_target = subbucket_targets_from_model(p, pl_total)

    # Caixa e outros aparecem no atual, mas não recebem alvo estratégico direto.
    actual_macro_raw = pos_cliente.groupby("classe_macro", dropna=False)["valor_mercado"].sum().reset_index()
    macro_alias = {"Caixa": "Saldo em Conta", "Outros": "Outros / Fora da Estratégia"}
    actual_macro_raw["Classe"] = actual_macro_raw["classe_macro"].replace(macro_alias)
    actual_macro = actual_macro_raw.groupby("Classe")["valor_mercado"].sum().reset_index().rename(columns={"valor_mercado": "Valor Atual"})

    macro_df = pd.merge(actual_macro, macro_target, on="Classe", how="outer").fillna(0)
    macro_df["Peso Atual"] = np.where(pl_total > 0, macro_df["Valor Atual"] / pl_total, 0)
    macro_df["Diferença"] = macro_df["Valor Ideal"] - macro_df["Valor Atual"]
    macro_df["Ação"] = macro_df["Diferença"].apply(acao_por_diff)
    macro_df = macro_df[["Classe", "Peso Atual", "Peso Ideal", "Valor Atual", "Valor Ideal", "Diferença", "Ação"]]

    actual_sub = pos_cliente.groupby(["classe_macro", "subbucket"], dropna=False)["valor_mercado"].sum().reset_index()
    actual_sub = actual_sub.rename(columns={"classe_macro": "Classe", "valor_mercado": "Valor Atual", "subbucket": "Subbucket"})
    sub_df = pd.merge(actual_sub, sub_target, on=["Classe", "Subbucket"], how="outer").fillna(0)
    sub_df["Peso Atual"] = np.where(pl_total > 0, sub_df["Valor Atual"] / pl_total, 0)
    sub_df["Diferença"] = sub_df["Valor Ideal"] - sub_df["Valor Atual"]
    sub_df["Ação"] = sub_df["Diferença"].apply(acao_por_diff)
    order_map = {b: i for i, b in enumerate(SUBBUCKET_ORDER)}
    sub_df["_ord"] = sub_df["Subbucket"].map(order_map).fillna(999)
    sub_df = sub_df.sort_values(["Classe", "_ord", "Subbucket"]).drop(columns="_ord")
    sub_df = sub_df[["Classe", "Subbucket", "Peso Atual", "Peso Ideal", "Valor Atual", "Valor Ideal", "Diferença", "Ação"]]
    return macro_df, sub_df


def rv_recommendation(pos_cliente: pd.DataFrame, p: dict[str, float], pl_total: float, modelo: str) -> pd.DataFrame:
    uni = rv_universe(modelo)
    rows = []
    for subbucket, tickers in uni.items():
        alvo_total = pl_total * peso_get(p, subbucket)
        alvo_por_ativo = alvo_total / len(tickers) if tickers else 0
        for t in tickers:
            atual = pos_cliente.loc[pos_cliente["asset_id"].astype(str).str.upper().eq(t), "valor_mercado"].sum()
            qtd = pos_cliente.loc[pos_cliente["asset_id"].astype(str).str.upper().eq(t), "quantidade"].sum()
            diff = alvo_por_ativo - atual
            rows.append([subbucket, t, qtd, atual, alvo_por_ativo, diff, acao_por_diff(diff)])
    return pd.DataFrame(rows, columns=["Subbucket", "Ativo", "Qtd Atual", "Valor Atual", "Valor Ideal", "Diferença", "Ação"])


def theoretical_portfolio(p: dict[str, float], valor: float, modelo: str) -> pd.DataFrame:
    sub = subbucket_targets_from_model(p, valor)
    rows = []
    for _, r in sub.iterrows():
        classe, bucket, w, val = r["Classe"], r["Subbucket"], r["Peso Ideal"], r["Valor Ideal"]
        rows.append([classe, bucket, "Subbucket", "", w, val, ""])
        # Abre RV por ativo somente para visualização estratégica.
        if bucket in ["Ações", "FIIs"]:
            tickers = rv_universe(modelo).get(bucket, [])
            if tickers:
                w_asset = w / len(tickers)
                val_asset = valor * w_asset
                for t in tickers:
                    rows.append([classe, bucket, "Ativo", t, w_asset, val_asset, "Carteira estratégica"])
        elif bucket == "FiInfra e Cetipados":
            tickers = FI_INFRA_TICKERS
            w_asset = w / len(tickers) if tickers else 0
            val_asset = valor * w_asset
            for t in tickers:
                rows.append([classe, bucket, "Ativo", t, w_asset, val_asset, "Lista estratégica"])
    return pd.DataFrame(rows, columns=["Classe", "Subbucket", "Nível", "Ativo", "Peso", "Valor", "Observação"])


def build_pdf_teorico(df_teor: pd.DataFrame, modelo: str, valor: float, cliente: str = "") -> BytesIO:
    if not HAS_REPORTLAB:
        raise RuntimeError("ReportLab não está instalado. Inclua reportlab no requirements.txt.")

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=1.2 * cm, leftMargin=1.2 * cm, topMargin=1.0 * cm, bottomMargin=1.0 * cm)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="MWTitle", parent=styles["Title"], fontSize=17, alignment=TA_CENTER, leading=21))
    styles.add(ParagraphStyle(name="MWSub", parent=styles["Normal"], fontSize=9, alignment=TA_CENTER, textColor=colors.HexColor("#555555")))
    styles.add(ParagraphStyle(name="MWSmall", parent=styles["Normal"], fontSize=7.4, leading=9))

    story = []
    lp = logo_path()
    if lp:
        story.append(Image(str(lp), width=5.0 * cm, height=1.6 * cm, kind="proportional"))
        story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph("Estudo de Carteira Teórica", styles["MWTitle"]))
    story.append(Paragraph(f"M Wealth • {datetime.now().strftime('%d/%m/%Y')}", styles["MWSub"]))
    story.append(Spacer(1, 0.35 * cm))

    info = [["Modelo", modelo], ["Valor simulado", format_brl(valor)]]
    if cliente.strip():
        info.insert(0, ["Cliente", cliente.strip()])
    info_tbl = Table(info, colWidths=[4 * cm, 12 * cm])
    info_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef1f6")),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d9d9d9")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(info_tbl)
    story.append(Spacer(1, 0.35 * cm))

    macro = df_teor[df_teor["Nível"].eq("Subbucket")].groupby("Classe", as_index=False)["Valor"].sum()
    macro["Peso"] = macro["Valor"] / valor if valor else 0
    macro_data = [["Classe", "Peso", "Valor"]] + [[r["Classe"], fmt_pct(r["Peso"]), format_brl(r["Valor"])] for _, r in macro.iterrows()]
    macro_tbl = Table(macro_data, colWidths=[7 * cm, 3 * cm, 5 * cm])
    macro_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#172b4d")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d9d9d9")),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
    ]))
    story.append(Paragraph("Resumo da Alocação", styles["Heading2"]))
    story.append(macro_tbl)
    story.append(Spacer(1, 0.35 * cm))

    sub = df_teor[df_teor["Nível"].eq("Subbucket")].copy()
    sub_data = [["Classe", "Subbucket", "Peso", "Valor"]]
    for _, r in sub.iterrows():
        sub_data.append([r["Classe"], r["Subbucket"], fmt_pct(r["Peso"]), format_brl(r["Valor"])])
    sub_tbl = Table(sub_data, colWidths=[3.1 * cm, 7.1 * cm, 2.2 * cm, 4.0 * cm], repeatRows=1)
    sub_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#172b4d")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.20, colors.HexColor("#dddddd")),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
    ]))
    story.append(Paragraph("Abertura por Subbucket", styles["Heading2"]))
    story.append(sub_tbl)

    ativos = df_teor[df_teor["Nível"].eq("Ativo")].copy()
    if not ativos.empty:
        story.append(PageBreak())
        story.append(Paragraph("Carteira Teórica por Ativo", styles["Heading2"]))
        ativo_data = [["Subbucket", "Ativo", "Peso", "Valor", "Observação"]]
        for _, r in ativos.iterrows():
            ativo_data.append([r["Subbucket"], r["Ativo"], fmt_pct(r["Peso"]), format_brl(r["Valor"]), r["Observação"]])
        ativo_tbl = Table(ativo_data, colWidths=[5.2 * cm, 3.0 * cm, 2.0 * cm, 3.4 * cm, 3.2 * cm], repeatRows=1)
        ativo_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#172b4d")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 6.8),
            ("GRID", (0, 0), (-1, -1), 0.20, colors.HexColor("#dddddd")),
            ("ALIGN", (2, 1), (3, -1), "RIGHT"),
        ]))
        story.append(ativo_tbl)

    disclaimer = (
        "Este material é meramente informativo e apresenta uma simulação de carteira teórica com base em parâmetros "
        "internos de alocação. A simulação não constitui promessa de rentabilidade, garantia de resultado, oferta pública "
        "ou recomendação individualizada sem a devida análise do perfil, objetivos, restrições e suitability do investidor. "
        "Rentabilidade passada não representa garantia de rentabilidade futura."
    )
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph("Disclaimer", styles["Heading3"]))
    story.append(Paragraph(disclaimer, styles["MWSmall"]))
    doc.build(story)
    buf.seek(0)
    return buf


# =============================================================================
# CARREGAMENTO INICIAL
# =============================================================================
df_contas = load_contas()
pesos = load_pesos_xlsx()

try:
    ptax, ptax_data = get_ptax_usdbrl_last()
    st.caption(f"💱 PTAX usada: **{ptax:.4f}** • {ptax_data}")
except Exception:
    ptax, ptax_data = 5.60, "fallback"
    st.warning(f"Não foi possível obter PTAX automática. Usando fallback de {ptax:.2f}.")

st.title("M Wealth - Asset Allocation")
st.caption("Consolidação de posições, diagnóstico operacional e alocação ideal por carteira.")

# Navegação lazy: evita que todas as abas sejam recalculadas a cada clique.
# st.tabs renderiza/executa todo o conteúdo de todas as abas em cada rerun;
# com radio, somente a página escolhida é executada. Isso reduz travamentos/tela branca.
with st.sidebar:
    lp = logo_path()
    if lp:
        st.image(str(lp), use_container_width=True)
    page = st.radio(
        "Navegação",
        ["📌 Posições", "🎯 Asset Allocation", "📄 Carteira Teórica"],
        index=0,
    )

# =============================================================================
# TAB 1 — POSIÇÕES
# =============================================================================
if page == "📌 Posições":
    st.header("Painel de Posições Consolidadas")
    col_a, col_b = st.columns([1, 4])
    force = col_a.button("Forçar atualização", type="primary")
    if force:
        st.cache_data.clear()
    df_latest, load_mode = get_latest_positions_auto(force=force)

    if df_latest is None or df_latest.empty:
        st.stop()

    df_latest = enrich_positions(df_latest)
    if load_mode == "cache":
        st.success("Base carregada do cache mais recente. Nenhum arquivo fonte novo foi detectado.")
    elif load_mode.startswith("rebuild"):
        st.success("Base reconstruída automaticamente a partir dos arquivos do repositório.")

    meta = getattr(df_latest, "attrs", {}).get("meta", {}) if hasattr(df_latest, "attrs") else {}
    dt_pos = meta.get("dt_posicao", "") if isinstance(meta, dict) else ""
    if dt_pos:
        st.caption(f"Data de posição registrada: **{dt_pos}**")

    df_latest["valor_mercado"] = pd.to_numeric(df_latest["valor_mercado"], errors="coerce").fillna(0.0)
    contas_distintas = df_latest[["corretora", "conta"]].drop_duplicates()
    grupos_distintos = df_latest["GRUPO GERAL"].dropna().nunique() if "GRUPO GERAL" in df_latest.columns else 0
    pl_total = float(df_latest["valor_mercado"].sum())
    pl_medio_grupo = pl_total / grupos_distintos if grupos_distintos else 0
    saldo_total = float(df_latest.loc[df_latest["subbucket"].eq("Saldo em Conta"), "valor_mercado"].sum())

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("PL Total", format_brl(pl_total), delta=f"{len(contas_distintas)} contas")
    c2.metric("Grupos familiares", f"{grupos_distintos}")
    c3.metric("PL médio por grupo", format_brl(pl_medio_grupo))
    c4.metric("Saldo em conta", format_brl(saldo_total))
    c5.metric("Ativos não classificados", int((df_latest["subbucket"] == "Outros / Não Classificado").sum()))

    resumo_corretora = df_latest.groupby("corretora", as_index=False).agg(
        PL=("valor_mercado", "sum"),
        Contas=("conta", "nunique"),
        Ativos=("asset_id", "nunique"),
    ).sort_values("PL", ascending=False)

    col1, col2 = st.columns(2)
    with col1:
        fig = px.pie(resumo_corretora, values="PL", names="corretora", title="PL por Corretora", hole=0.45)
        fig.update_layout(height=330, margin=dict(l=10, r=10, t=50, b=10))
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        dist = df_latest.groupby("subbucket", as_index=False)["valor_mercado"].sum().sort_values("valor_mercado", ascending=False).head(15)
        fig2 = px.bar(dist, x="valor_mercado", y="subbucket", orientation="h", title="Distribuição Global por Estratégia/Subbucket")
        fig2.update_layout(height=330, yaxis={"categoryorder": "total ascending"}, margin=dict(l=10, r=10, t=50, b=10))
        st.plotly_chart(fig2, use_container_width=True)

    st.subheader("Resumo por Corretora")
    st.dataframe(
        resumo_corretora.style.format({"PL": format_brl}),
        use_container_width=True,
        hide_index=True,
    )

    col3, col4 = st.columns(2)
    with col3:
        st.subheader("Top 10 grupos por PL")
        if "GRUPO GERAL" in df_latest.columns:
            top_grupos = df_latest.groupby("GRUPO GERAL", as_index=False)["valor_mercado"].sum().sort_values("valor_mercado", ascending=False).head(10)
            top_grupos.columns = ["Grupo", "PL"]
            st.dataframe(top_grupos.style.format({"PL": format_brl}), use_container_width=True, hide_index=True)
        else:
            st.info("Coluna GRUPO GERAL não encontrada na base consolidada.")
    with col4:
        st.subheader("Controle de Qualidade")
        sem_grupo = int(df_latest["GRUPO GERAL"].isna().sum()) if "GRUPO GERAL" in df_latest.columns else len(df_latest)
        sem_cliente = int(df_latest["CLIENTE"].isna().sum()) if "CLIENTE" in df_latest.columns else len(df_latest)
        outros_val = float(df_latest.loc[df_latest["subbucket"].eq("Outros / Não Classificado"), "valor_mercado"].sum())
        qc = pd.DataFrame({
            "Item": ["Linhas sem grupo", "Linhas sem cliente", "Valor não classificado", "PL em saldo"],
            "Resultado": [sem_grupo, sem_cliente, format_brl(outros_val), format_brl(saldo_total)],
        })
        st.dataframe(qc, use_container_width=True, hide_index=True)

    with st.expander("Ver ativos consolidados", expanded=False):
        cols = ["corretora", "conta", "GRUPO GERAL", "CLIENTE", "asset_id", "asset_nome", "asset_tipo", "classe_macro", "subbucket", "valor_mercado", "quantidade", "moeda"]
        view = safe_cols(df_latest, cols).sort_values("valor_mercado", ascending=False)
        st.dataframe(view.style.format({"valor_mercado": format_brl, "quantidade": "{:,.2f}"}), use_container_width=True, hide_index=True)

    with st.expander("Ativos / linhas sem classificação", expanded=False):
        unc = df_latest[df_latest["subbucket"].eq("Outros / Não Classificado")]
        if unc.empty:
            st.success("Nenhuma linha sem classificação relevante encontrada.")
        else:
            st.dataframe(safe_cols(unc, ["corretora", "conta", "GRUPO GERAL", "asset_id", "asset_nome", "asset_tipo", "mercado", "sub_mercado", "valor_mercado"]).style.format({"valor_mercado": format_brl}), use_container_width=True, hide_index=True)

# =============================================================================
# TAB 2 — ASSET ALLOCATION
# =============================================================================
if page == "🎯 Asset Allocation":
    st.header("Asset Allocation - Cliente / Grupo Familiar")
    df_latest, _ = get_latest_positions_auto(force=False)
    if df_latest is None or df_latest.empty:
        st.warning("Não foi possível carregar a base de posições. Entre na página 'Posições' e clique em Forçar atualização.")
        st.stop()

    # Sempre reclassifica para limpar qualquer coluna duplicada que tenha vindo de cache/salvamento.
    df_latest = enrich_positions(df_latest.copy())

    col_g, col_c, col_m = st.columns([3, 3, 2])
    with col_g:
        grupos = sorted([g for g in df_latest.get("GRUPO GERAL", pd.Series(dtype=str)).dropna().unique()])
        grupo_sel = st.selectbox("👥 Grupo Geral", grupos) if grupos else None

    if not grupo_sel:
        st.warning("Não há grupos carregados na base.")
        st.stop()

    contas_info = df_latest[df_latest["GRUPO GERAL"] == grupo_sel][["conta", "CLIENTE", "corretora"]].drop_duplicates()
    with col_c:
        opcoes = ["Todas as contas"]
        for _, row in contas_info.iterrows():
            nome = str(row.get("CLIENTE", "")).strip()
            conta = str(row.get("conta", "")).strip()
            corr = str(row.get("corretora", "")).strip()
            opcoes.append(f"{nome} • {corr} ({conta})" if nome else f"{corr} ({conta})")
        selecao = st.selectbox("Conta", opcoes)

    if selecao == "Todas as contas":
        pos_cliente = df_latest[df_latest["GRUPO GERAL"] == grupo_sel].copy()
    else:
        conta_real = selecao.split("(")[-1].strip(")")
        pos_cliente = df_latest[(df_latest["GRUPO GERAL"] == grupo_sel) & (df_latest["conta"].astype(str) == conta_real)].copy()

    with col_m:
        perfil_cliente = "Não identificado"
        if not df_contas.empty and "GRUPO GERAL" in df_contas.columns:
            m = df_contas[df_contas["GRUPO GERAL"].astype(str).str.strip().eq(str(grupo_sel).strip())]
            if not m.empty:
                col_perfil = next((c for c in m.columns if norm(c) == "PERFIL CARTEIRA"), None)
                if col_perfil:
                    perfil_cliente = str(m[col_perfil].iloc[0]).strip()
        modelo_default = model_for_profile(perfil_cliente, list(pesos.keys()))
        idx = list(pesos.keys()).index(modelo_default) if modelo_default in pesos else 0
        modelo = st.selectbox("Modelo de alocação", list(pesos.keys()), index=idx)

    p = pesos[modelo]
    pl_cliente = float(pd.to_numeric(pos_cliente["valor_mercado"], errors="coerce").fillna(0).sum())
    pos_cliente = enrich_positions(pos_cliente)

    pl_xp = pos_cliente.loc[pos_cliente["corretora"].eq("XP"), "valor_mercado"].sum()
    pl_btg = pos_cliente.loc[pos_cliente["corretora"].eq("BTG"), "valor_mercado"].sum()
    pl_cs = pos_cliente.loc[pos_cliente["corretora"].eq("CS"), "valor_mercado"].sum()
    saldo = pos_cliente.loc[pos_cliente["subbucket"].eq("Saldo em Conta"), "valor_mercado"].sum()
    fora = pos_cliente.loc[pos_cliente["classe_macro"].isin(["Outros", "Caixa"]), "valor_mercado"].sum()

    st.markdown('<div class="mw-divider"></div>', unsafe_allow_html=True)
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("PL Total", format_brl(pl_cliente), delta=f"Perfil: {perfil_cliente}")
    k2.metric("XP", format_brl(pl_xp))
    k3.metric("BTG", format_brl(pl_btg))
    k4.metric("CS", format_brl(pl_cs), delta=format_usd(pl_cs / ptax) if ptax else "")
    k5.metric("Saldo / Fora Estratégia", format_brl(saldo + fora))

    macro_df, sub_df = portfolio_actual_tables(pos_cliente, p, pl_cliente)

    st.subheader("1. Visão Macro Geral")
    col_chart1, col_chart2 = st.columns(2)
    with col_chart1:
        fig = px.pie(macro_df[macro_df["Valor Atual"] > 0], names="Classe", values="Valor Atual", title="Carteira Atual", hole=0.45)
        fig.update_layout(height=330, margin=dict(l=10, r=10, t=45, b=10))
        st.plotly_chart(fig, use_container_width=True)
    with col_chart2:
        ideal_plot = macro_df[macro_df["Valor Ideal"] > 0]
        fig = px.pie(ideal_plot, names="Classe", values="Valor Ideal", title="Carteira Ideal", hole=0.45)
        fig.update_layout(height=330, margin=dict(l=10, r=10, t=45, b=10))
        st.plotly_chart(fig, use_container_width=True)

    macro_view = macro_df.copy()
    st.dataframe(
        macro_view.style.format({"Peso Atual": fmt_pct, "Peso Ideal": fmt_pct, "Valor Atual": format_brl, "Valor Ideal": format_brl, "Diferença": format_brl})
        .map(style_diff, subset=["Diferença"])
        .map(style_status, subset=["Ação"]),
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("2. Subbuckets da Alocação")
    st.caption("Essa tabela é a ponte operacional entre a visão macro e os ativos. Ela mostra onde há falta ou excesso por estratégia.")
    st.dataframe(
        sub_df.style.format({"Peso Atual": fmt_pct, "Peso Ideal": fmt_pct, "Valor Atual": format_brl, "Valor Ideal": format_brl, "Diferença": format_brl})
        .map(style_diff, subset=["Diferença"])
        .map(style_status, subset=["Ação"]),
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("3. Detalhamento das Posições por Estratégia")
    for bucket in [b for b in SUBBUCKET_ORDER if b in set(pos_cliente["subbucket"])]:
        subpos = pos_cliente[pos_cliente["subbucket"].eq(bucket)].copy()
        if subpos.empty:
            continue
        total_bucket = subpos["valor_mercado"].sum()
        with st.expander(f"{bucket} • {format_brl(total_bucket)}", expanded=bucket in ["Ações", "FIIs", "Outros / Não Classificado"]):
            agrup = subpos.groupby(["asset_id", "asset_nome", "corretora"], dropna=False, as_index=False).agg(
                Valor=("valor_mercado", "sum"),
                Quantidade=("quantidade", "sum"),
                Contas=("conta", "nunique"),
            ).sort_values("Valor", ascending=False)
            agrup["Peso no Cliente"] = np.where(pl_cliente > 0, agrup["Valor"] / pl_cliente, 0)
            st.dataframe(agrup.style.format({"Valor": format_brl, "Quantidade": "{:,.2f}", "Peso no Cliente": fmt_pct}), use_container_width=True, hide_index=True)

    st.subheader("4. Sugestão Estratégica de RV Brasil")
    rv_df = rv_recommendation(pos_cliente, p, pl_cliente, modelo)
    colrv1, colrv2 = st.columns(2)
    with colrv1:
        total_rv_atual = pos_cliente.loc[pos_cliente["classe_macro"].eq("RV Brasil"), "valor_mercado"].sum()
        total_rv_alvo = pl_cliente * peso_get(p, "RV Brasil")
        st.metric("RV Atual", format_brl(total_rv_atual))
    with colrv2:
        st.metric("RV Ideal", format_brl(total_rv_alvo), delta=format_brl(total_rv_alvo - total_rv_atual))
    st.dataframe(
        rv_df.style.format({"Qtd Atual": "{:,.2f}", "Valor Atual": format_brl, "Valor Ideal": format_brl, "Diferença": format_brl})
        .map(style_diff, subset=["Diferença"])
        .map(style_status, subset=["Ação"]),
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("Ativos fora da estratégia / que merecem revisão", expanded=False):
        universo = set(ACOES_SEM_RENDA + ACOES_COM_RENDA + FIIS_RECOMENDADOS + FI_INFRA_TICKERS)
        fora_estrat = pos_cliente[
            pos_cliente["classe_macro"].isin(["Outros", "Caixa"]) |
            ((pos_cliente["classe_macro"].eq("RV Brasil")) & (~pos_cliente["asset_id"].astype(str).str.upper().isin(universo))) |
            (pos_cliente["subbucket"].str.contains("Sem Liquidez|Não Classificado|COE|Previdência", case=False, na=False))
        ].copy()
        if fora_estrat.empty:
            st.success("Nenhum item relevante fora da estratégia foi identificado.")
        else:
            st.dataframe(safe_cols(fora_estrat, ["corretora", "conta", "asset_id", "asset_nome", "classe_macro", "subbucket", "valor_mercado", "quantidade"]).style.format({"valor_mercado": format_brl, "quantidade": "{:,.2f}"}), use_container_width=True, hide_index=True)

# =============================================================================
# TAB 3 — CARTEIRA TEÓRICA
# =============================================================================
if page == "📄 Carteira Teórica":
    st.header("Carteira Teórica - Simulador para Visualização")
    st.caption("Ferramenta para o consultor entender como ficaria uma carteira de determinado valor em uma estratégia. A estratégia permanece fixa no código/gestão.")

    col1, col2, col3 = st.columns([2, 1.2, 2])
    with col1:
        modelo_teor = st.selectbox("Modelo", list(pesos.keys()), key="modelo_teorico")
    with col2:
        valor_teor = st.number_input("Patrimônio simulado", min_value=0.0, value=1_000_000.0, step=100_000.0, format="%.2f")
    with col3:
        cliente_pdf = st.text_input("Nome do cliente no PDF (opcional)")

    p_teor = pesos[modelo_teor]
    df_teor = theoretical_portfolio(p_teor, valor_teor, modelo_teor)

    macro_teor = df_teor[df_teor["Nível"].eq("Subbucket")].groupby("Classe", as_index=False)["Valor"].sum()
    macro_teor["Peso"] = np.where(valor_teor > 0, macro_teor["Valor"] / valor_teor, 0)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Valor simulado", format_brl(valor_teor))
    m2.metric("RF Brasil", format_brl(macro_teor.loc[macro_teor["Classe"].eq("RF Brasil"), "Valor"].sum()))
    m3.metric("RV Brasil", format_brl(macro_teor.loc[macro_teor["Classe"].eq("RV Brasil"), "Valor"].sum()))
    m4.metric("Internacional", format_brl(macro_teor.loc[macro_teor["Classe"].eq("Internacional"), "Valor"].sum()))

    c1, c2 = st.columns([1, 1])
    with c1:
        fig = px.pie(macro_teor, values="Valor", names="Classe", title="Alocação Teórica Macro", hole=0.45)
        fig.update_layout(height=330, margin=dict(l=10, r=10, t=45, b=10))
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        sub_chart = df_teor[df_teor["Nível"].eq("Subbucket")].sort_values("Valor", ascending=True)
        fig = px.bar(sub_chart, x="Valor", y="Subbucket", color="Classe", orientation="h", title="Abertura por Subbucket")
        fig.update_layout(height=330, margin=dict(l=10, r=10, t=45, b=10))
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Tabela Teórica Completa")
    st.dataframe(
        df_teor.style.format({"Peso": fmt_pct, "Valor": format_brl}),
        use_container_width=True,
        hide_index=True,
    )

    try:
        pdf = build_pdf_teorico(df_teor, modelo_teor, valor_teor, cliente_pdf)
        st.download_button(
            "Baixar PDF da Carteira Teórica",
            data=pdf,
            file_name=f"carteira_teorica_mwealth_{modelo_teor.lower().replace(' ', '_')}.pdf",
            mime="application/pdf",
            type="primary",
        )
    except Exception as e:
        st.info(f"PDF indisponível no ambiente atual: {e}")

st.caption("M Wealth Asset Allocation • Versão operacional")
