from __future__ import annotations

import json
import math
import unicodedata
from datetime import datetime
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

import positions as posmod

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    HAS_REPORTLAB = True
except Exception:
    HAS_REPORTLAB = False

PDF_FONT_REGULAR = "Helvetica"
PDF_FONT_BOLD = "Helvetica-Bold"


def register_pdf_fonts() -> tuple[str, str]:
    """Usa fonte Unicode do ambiente para preservar acentos no PDF."""
    global PDF_FONT_REGULAR, PDF_FONT_BOLD
    if not HAS_REPORTLAB:
        return PDF_FONT_REGULAR, PDF_FONT_BOLD
    candidates = [
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
    ]
    for regular, bold in candidates:
        if Path(regular).exists() and Path(bold).exists():
            try:
                pdfmetrics.registerFont(TTFont("MWRegular", regular))
                pdfmetrics.registerFont(TTFont("MWBold", bold))
                PDF_FONT_REGULAR = "MWRegular"
                PDF_FONT_BOLD = "MWBold"
                break
            except Exception:
                pass
    return PDF_FONT_REGULAR, PDF_FONT_BOLD


st.set_page_config(page_title="M Wealth | Balanceamento", layout="wide", page_icon="📊")

BASE_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
POS_DIR = BASE_DIR / "posicoes"
APP_VERSION = "3.2"

# Estratégia de RV e FiInfra permanece no código, conforme orientação da gestão.
ACOES_SEM_RENDA = ["AXIA3", "EQTL3", "SBSP3", "ITUB3", "BPAC11", "PSSA3", "PRIO3", "VALE3", "WEGE3", "RENT3"]
ACOES_COM_RENDA = ["CPLE3", "EGIE3", "AXIA3", "ITUB3", "VALE3", "ALOS3", "FLRY3", "ABEV3", "PRIO3", "WEGE3"]
FIIS_RECOMENDADOS = ["KNRI11", "XPML11", "HGLG11", "PVBI11", "HGRU11", "KNCR11", "KNIP11", "KNCA11"]
FI_INFRA_TICKERS = ["KNDI11", "CDII11", "IFRI11", "AZQI11", "KNCE11", "AZIN11", "JURO11", "IFRA11", "KDIF11", "JGPI11", "BDIF11", "JMBI11", "CPTI11"]
FI_INFRA_POS_TICKERS = ["KNDI11", "CDII11", "IFRI11", "AZQI11", "KNCE11", "AZIN11"]
FI_INFRA_INFLACAO_TICKERS = ["JURO11", "IFRA11", "KDIF11", "JGPI11", "BDIF11", "JMBI11", "CPTI11"]

# Mantém ordem operacional das tabelas.
SUBBUCKET_ORDER = [
    "Pós - Imediato", "Pós - 1 a 30 dias", "Pós - 31 a 180 dias", "Pós - 181 a 360 dias", "Pós - 361+ dias",
    "FiInfra e Cetipados", "Pré - Bancário", "Pré - Tesouro", "Inflação - Bancário", "Inflação - Tesouro", "Crédito Privado",
    "Ações", "FIIs", "Renda Fixa Internacional", "Renda Variável Internacional", "Caixa Internacional",
    "Saldo em Conta", "Fundos de Investimento / Sem Liquidez Mapeada", "Previdência", "COE / Estruturados", "Outros / Não Classificado",
]

st.markdown(
    """
    <style>
    .block-container { padding-top: 1.0rem; padding-bottom: 2.0rem; max-width: 1600px; }
    div[data-testid="stMetricValue"] { font-size: 1.2rem; }
    .mw-card { border: 1px solid rgba(255,255,255,.09); border-radius: 14px; padding: 14px 16px; background: rgba(255,255,255,.025); }
    .mw-muted { color: rgba(250,250,250,.65); font-size: .86rem; }
    .mw-ok { color: #77dd77; font-weight: 700; }
    .mw-warn { color: #ffd166; font-weight: 700; }
    .mw-bad { color: #ff6b6b; font-weight: 700; }
    .mw-line { border-top: 1px solid rgba(255,255,255,.10); margin: .9rem 0 1.1rem 0; }
    </style>
    """,
    unsafe_allow_html=True,
)


# =============================================================================
# Utilitários
# =============================================================================
def find_file(filename: str) -> Path:
    for p in [POS_DIR / filename, BASE_DIR / filename, Path.cwd() / filename, Path(filename)]:
        if p.exists():
            return p
    return POS_DIR / filename


def logo_path() -> Path | None:
    for p in [BASE_DIR / "Logo-M-Wealth.png", POS_DIR / "Logo-M-Wealth.png", Path.cwd() / "Logo-M-Wealth.png"]:
        if p.exists():
            return p
    return None


def logo_pdf_path() -> Path | None:
    """Logo específica para PDF em fundo claro."""
    for p in [BASE_DIR / "Logo-M-Wealth-Preta.png", POS_DIR / "Logo-M-Wealth-Preta.png", Path.cwd() / "Logo-M-Wealth-Preta.png", BASE_DIR / "Logo-M-Wealth.png"]:
        if p.exists():
            return p
    return None


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(s)) if not unicodedata.combining(c))


def norm(x) -> str:
    return strip_accents(str(x or "")).upper().strip()


def ticker_clean(x) -> str:
    return norm(x).replace(" ", "").replace(".", "")


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


def acao_por_diff(diff: float, tolerancia: float = 300.0) -> str:
    if diff > tolerancia:
        return "Comprar / Aportar"
    if diff < -tolerancia:
        return "Vender / Reduzir"
    return "Manter / OK"


def status_por_diff(diff: float, base: float, tolerancia_abs: float = 300.0, tolerancia_pct: float = 0.003) -> str:
    tol = max(tolerancia_abs, abs(base) * tolerancia_pct)
    if diff > tol:
        return "🟢 Falta comprar"
    if diff < -tol:
        return "🔴 Excesso"
    return "✅ OK"


def prioridade_por_diff(diff: float, pl: float) -> str:
    if pl <= 0:
        return "Baixa"
    impacto = abs(diff) / pl
    if impacto >= 0.05:
        return "Alta"
    if impacto >= 0.015:
        return "Média"
    return "Baixa"


def prepare_display(
    df: pd.DataFrame,
    money_cols: list[str] | None = None,
    pct_cols: list[str] | None = None,
    qty_cols: list[str] | None = None,
    max_rows: int | None = None,
) -> pd.DataFrame:
    """Evita pandas Styler. Tabelas ficam mais leves e estáveis no Streamlit."""
    out = df.copy()
    if max_rows is not None:
        out = out.head(max_rows).copy()
    for c in money_cols or []:
        if c in out.columns:
            out[c] = out[c].apply(format_brl)
    for c in pct_cols or []:
        if c in out.columns:
            out[c] = out[c].apply(fmt_pct)
    for c in qty_cols or []:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0).map(
                lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            )
    return out


@st.cache_data(show_spinner=False)
def load_pesos_xlsx(path_str: str) -> dict[str, dict[str, float]]:
    path = Path(path_str)
    if not path.exists():
        return {}
    xls = pd.ExcelFile(path, engine="openpyxl")
    df = pd.read_excel(xls, sheet_name=xls.sheet_names[0], header=None).fillna("")
    pesos: dict[str, dict[str, float]] = {}
    carteira = None
    for _, row in df.iterrows():
        a = str(row.iloc[0]).strip()
        b = str(row.iloc[1]).strip() if len(row) > 1 else ""
        if not a and not b:
            continue
        if b.lower() == "neutro" and a:
            carteira = a
            pesos.setdefault(carteira, {})
            continue
        if carteira is None or not a:
            continue
        try:
            w = float(str(row.iloc[1]).replace(",", ".").strip())
        except Exception:
            w = 0.0
        # Mantém chaves iguais somadas, caso a planilha repita conceitos.
        pesos[carteira][a] = pesos[carteira].get(a, 0.0) + w
    return {k: v for k, v in pesos.items() if v}


@st.cache_data(show_spinner=False)
def load_contas_cached() -> pd.DataFrame:
    try:
        return posmod.load_control_accounts()
    except Exception:
        return pd.DataFrame()


@st.cache_data(show_spinner="Carregando base consolidada...")
def load_positions_cached(force_rebuild: bool = False) -> tuple[pd.DataFrame, dict, str]:
    if force_rebuild:
        df = posmod.build_latest_from_repo()
        mode = "rebuild_manual"
    else:
        if posmod.latest_is_stale():
            df = posmod.build_latest_from_repo()
            mode = "rebuild_auto"
        else:
            df = posmod.load_latest_positions()
            if df is None or df.empty:
                df = posmod.build_latest_from_repo()
                mode = "rebuild_empty"
            else:
                mode = "cache"
    meta = getattr(df, "attrs", {}).get("meta", {}) if hasattr(df, "attrs") else {}
    return df, meta, mode


# =============================================================================
# Classificação central dos ativos
# =============================================================================
def classify_position(row: pd.Series) -> pd.Series:
    corretora = norm(row.get("corretora", ""))
    asset_id = ticker_clean(row.get("asset_id", ""))
    asset_tipo = norm(row.get("asset_tipo", ""))
    mercado = norm(row.get("mercado", ""))
    sub_mercado = norm(row.get("sub_mercado", ""))
    estrategia = norm(row.get("estrategia", ""))
    indexador = norm(row.get("indexador", ""))
    liquidez = norm(row.get("liquidez", ""))
    emissor = norm(row.get("emissor", ""))
    taxa = norm(row.get("taxa", ""))
    nome = norm(row.get("asset_nome", ""))
    text = " ".join([asset_id, nome, asset_tipo, mercado, sub_mercado, estrategia, indexador, liquidez, emissor, taxa])

    # Saldo operacional real: vem marcado no parser a partir da origem correta
    # XP = Financeiro/ValorDisponivel; BTG = Conta Corrente; CS = Cash quando houver linha de cash.
    if bool(row.get("saldo_operacional", False)):
        if corretora == "CS":
            return pd.Series(["Internacional", "Caixa Internacional", "Caixa Internacional", "Operacional"])
        return pd.Series(["Caixa", "Saldo em Conta", "Saldo em Conta", "Operacional"])

    # Internacional
    if corretora == "CS":
        if any(x in text for x in ["CASH", "MONEY MARKET", "SWEEP", "BANK DEPOSIT"]):
            return pd.Series(["Internacional", "Caixa Internacional", "Caixa Internacional", "Operacional"])
        if any(x in text for x in ["BOND", "FIXED", "TREASURY", "CD ", "CERTIFICATE", "NOTE", "CORPORATE"]):
            return pd.Series(["Internacional", "Renda Fixa Internacional", "Renda Fixa Internacional", "Estratégia"])
        return pd.Series(["Internacional", "Renda Variável Internacional", "Renda Variável Internacional", "Estratégia"])

    # Caixa / saldo - fallback conservador. Não inclui Custódia Remunerada, proventos ou outros itens.
    if asset_id == "SALDOFINANCEIRO" or (mercado == "CONTA CORRENTE" and nome == "CONTA CORRENTE"):
        return pd.Series(["Caixa", "Saldo em Conta", "Saldo em Conta", "Operacional"])

    # Previdência / COE / estruturados
    if any(x in text for x in ["PREVIDENCIA", "PGBL", "VGBL", "PREV "]):
        return pd.Series(["Fora da Estratégia", "Previdência", "Previdência", "Fora da Estratégia"])
    if any(x in text for x in ["COE", "ESTRUTURADO", "OPCOES FLEX", "OPCAO FLEX", "OPCOES", "OPÇÃO"]):
        return pd.Series(["Fora da Estratégia", "COE / Estruturados", "COE / Estruturados", "Fora da Estratégia"])

    # Fi-Infra: precisa vir antes de FII porque todos terminam em 11.
    if asset_id in FI_INFRA_TICKERS or any(x in text for x in ["FI INFRA", "FIINFRA", "FIC INFR", "INFRA", "DEB INCENTIVADA"]):
        return pd.Series(["RF Brasil", "FiInfra e Cetipados", "FiInfra e Cetipados", "Estratégia"])

    # RV Brasil
    if asset_tipo in ["FUNDOS IMOBILIARIOS", "FUNDOS IMOBILIÁRIOS"] or any(x in text for x in ["FUNDO IMOB", "FII", "FUNDO IMOBILIARIO"]):
        return pd.Series(["RV Brasil", "FIIs", "FIIs", "Estratégia"])
    if (asset_id.endswith("11") and len(asset_id) >= 5 and asset_id[:4].isalpha()):
        return pd.Series(["RV Brasil", "FIIs", "FIIs", "Estratégia"])
    if asset_tipo in ["ACOES", "AÇÕES"] or any(x in text for x in ["ACAO", "AÇÃO", "BOVESPA", "RENDA VARIAVEL"]):
        return pd.Series(["RV Brasil", "Ações", "Ações", "Estratégia"])
    if len(asset_id) in [5, 6] and asset_id[:4].isalpha() and asset_id[-1].isdigit():
        return pd.Series(["RV Brasil", "Ações", "Ações", "Estratégia"])

    # RF Brasil: Tesouro e indexadores
    if any(x in text for x in ["TESOURO SELIC", "LFT", "SELIC"]):
        return pd.Series(["RF Brasil", "Pós - Imediato", "Pós - Imediato", "Estratégia"])
    if any(x in text for x in ["TESOURO PRE", "NTN-F", "NTNF", "LTN"]):
        return pd.Series(["RF Brasil", "Pré - Tesouro", "Pré - Tesouro", "Estratégia"])
    if any(x in text for x in ["TESOURO IPCA", "NTN-B", "NTNB", "NTNB PRINC"]):
        return pd.Series(["RF Brasil", "Inflação - Tesouro", "Inflação - Tesouro", "Estratégia"])

    # Prazo/liquidez dos pós-fixados quando informado.
    if any(x in liquidez for x in ["D+0", "D+1", "LIQUIDEZ DIARIA", "LIQUIDEZ DIÁRIA"]) or any(x in text for x in ["IMEDIATO", "COMPROMISSADA"]):
        return pd.Series(["RF Brasil", "Pós - Imediato", "Pós - Imediato", "Estratégia"])
    if "D+30" in liquidez or "D+31" in liquidez or "D+59" in liquidez:
        return pd.Series(["RF Brasil", "Pós - 31 a 180 dias", "Pós - 31 a 180 dias", "Estratégia"])

    # Crédito privado antes de CDB/LCI/LCA genéricos.
    if any(x in text for x in ["CRI", "CRA", "DEB", "DEBENTURE", "CDCA", "CREDITO PRIVADO", "CRÉDITO PRIVADO"]):
        if any(x in text for x in ["IPCA", "IPC-A", "INFLACAO", "INFLAÇÃO"]):
            return pd.Series(["RF Brasil", "Inflação - Bancário", "Inflação - Bancário", "Estratégia"])
        if any(x in text for x in ["PRE-FIXADO", "PRE FIXADO", "PRÉ-FIXADO", "PREFIXADO"]):
            return pd.Series(["RF Brasil", "Pré - Bancário", "Pré - Bancário", "Estratégia"])
        return pd.Series(["RF Brasil", "Crédito Privado", "Crédito Privado", "Estratégia"])

    if any(x in text for x in ["IPCA", "IPC-A", "INFLACAO", "INFLAÇÃO"]):
        return pd.Series(["RF Brasil", "Inflação - Bancário", "Inflação - Bancário", "Estratégia"])
    if any(x in text for x in ["PRE-FIXADO", "PRE FIXADO", "PRÉ-FIXADO", "PREFIXADO", "PRE FIX", "PRÉ FIX"]):
        return pd.Series(["RF Brasil", "Pré - Bancário", "Pré - Bancário", "Estratégia"])
    if any(x in text for x in ["CDB", "LCI", "LCA", "LCD", "CDI", "POS-FIXADO", "PÓS-FIXADO", "POS FIXADO", "PÓS FIXADO"]):
        return pd.Series(["RF Brasil", "Pós - 361+ dias", "Pós - 361+ dias", "Estratégia"])

    # Fundos não são classe operacional de compra/venda; ficam monitorados.
    if any(x in text for x in ["FIC", "FIM", "FIRF", "FIDC", "FUNDO", "FUNDOS"]):
        return pd.Series(["Fora da Estratégia", "Fundos de Investimento / Sem Liquidez Mapeada", "Fundos de Investimento / Sem Liquidez Mapeada", "Monitorar"])

    return pd.Series(["Fora da Estratégia", "Outros / Não Classificado", "Outros / Não Classificado", "Revisar"])


@st.cache_data(show_spinner=False)
def enrich_positions_cached(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df = df.loc[:, ~df.columns.duplicated()].copy()
    df = df.drop(columns=[c for c in ["classe_macro", "subclasse", "subbucket", "tratamento"] if c in df.columns], errors="ignore")
    for c in ["valor_mercado", "quantidade", "valor_original"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    cols = df.apply(classify_position, axis=1)
    cols.columns = ["classe_macro", "subclasse", "subbucket", "tratamento"]
    out = pd.concat([df, cols], axis=1)
    out = out.loc[:, ~out.columns.duplicated()].copy()
    out["ticker_norm"] = out["asset_id"].apply(ticker_clean)
    return out


# =============================================================================
# Modelo/targets
# =============================================================================
def peso_get(p: dict[str, float], key: str) -> float:
    key_n = norm(key)
    for k, v in p.items():
        if norm(k) == key_n:
            return float(v or 0)
    return 0.0


def model_for_profile(perfil: str, modelos: list[str]) -> str:
    pn = norm(perfil)
    candidates = []
    if "ARROJADO RENDA CONSTRUCAO" in pn: candidates.append("Arrojado Renda Construção")
    if "MODERADO RENDA CONSTRUCAO" in pn: candidates.append("Moderado Renda Construção")
    if "CONSERVADOR RENDA CONSTRUCAO" in pn: candidates.append("Conservador Renda Construção")
    if "ARROJADO RENDA USUFRUTO" in pn: candidates.append("Arrojado Renda Usufruto")
    if "MODERADO RENDA USUFRUTO" in pn: candidates.append("Moderado Renda Usufruto")
    if "CONSERVADOR RENDA USUFRUTO" in pn: candidates.append("Conservador Renda Usufruto")
    if "ULTRACONSERVADOR" in pn: candidates.append("Ultraconservador")
    if "CONSERVADOR" in pn: candidates.append("Conservador")
    if "MODERADO" in pn: candidates.append("Moderado")
    if "ARROJADO" in pn: candidates.append("Arrojado")
    for c in candidates:
        for m in modelos:
            if norm(m) == norm(c):
                return m
    return modelos[0] if modelos else ""


def subbucket_targets_from_model(p: dict[str, float], pl: float) -> pd.DataFrame:
    rows = [
        ("RF Brasil", "Pós - Imediato", peso_get(p, "Imediato")),
        ("RF Brasil", "Pós - 1 a 30 dias", peso_get(p, "1 a 30 dias")),
        ("RF Brasil", "Pós - 31 a 180 dias", peso_get(p, "31 a 180 dias")),
        ("RF Brasil", "Pós - 181 a 360 dias", peso_get(p, "181 a 360 dias")),
        ("RF Brasil", "Pós - 361+ dias", peso_get(p, "361+ dias")),
        ("RF Brasil", "FiInfra e Cetipados", peso_get(p, "FiInfra e Cetipados") + peso_get(p, "FiInfra e Cetipado")),
        ("RF Brasil", "Pré - Bancário", peso_get(p, "Bancário Pré")),
        ("RF Brasil", "Pré - Tesouro", peso_get(p, "Tesouro Pré")),
        ("RF Brasil", "Inflação - Bancário", peso_get(p, "Bancário")),
        ("RF Brasil", "Inflação - Tesouro", peso_get(p, "Tesouro")),
        ("RF Brasil", "Crédito Privado", peso_get(p, "Crédito Privado")),
        ("RV Brasil", "Ações", peso_get(p, "Ações")),
        ("RV Brasil", "FIIs", peso_get(p, "FIIs")),
        ("Internacional", "Renda Fixa Internacional", peso_get(p, "Renda Fixa")),
        ("Internacional", "Renda Variável Internacional", peso_get(p, "Renda Variável")),
    ]
    df = pd.DataFrame(rows, columns=["Classe", "Subbucket", "Peso Ideal"])
    df = df[df["Peso Ideal"] > 0].copy()
    df["Valor Ideal"] = df["Peso Ideal"] * pl
    return df


def macro_targets_from_sub(subtarget: pd.DataFrame, pl: float, p: dict[str, float]) -> pd.DataFrame:
    if subtarget.empty:
        rv = peso_get(p, "RV Brasil")
        intl = peso_get(p, "Internacional")
        rf = max(0.0, 1.0 - rv - intl)
        return pd.DataFrame({"Classe": ["RF Brasil", "RV Brasil", "Internacional"], "Peso Ideal": [rf, rv, intl], "Valor Ideal": [rf*pl, rv*pl, intl*pl]})
    macro = subtarget.groupby("Classe", as_index=False).agg({"Peso Ideal": "sum", "Valor Ideal": "sum"})
    return macro


def rv_universe(modelo: str) -> dict[str, list[str]]:
    return {"Ações": ACOES_COM_RENDA if "RENDA" in norm(modelo) else ACOES_SEM_RENDA, "FIIs": FIIS_RECOMENDADOS}


def portfolio_tables(pos_cliente: pd.DataFrame, p: dict[str, float], pl: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    sub_target = subbucket_targets_from_model(p, pl)
    macro_target = macro_targets_from_sub(sub_target, pl, p)

    actual_macro = pos_cliente.groupby("classe_macro", as_index=False)["valor_mercado"].sum().rename(columns={"classe_macro": "Classe", "valor_mercado": "Valor Atual"})
    macro = actual_macro.merge(macro_target, how="outer", on="Classe").fillna(0)
    macro["Peso Atual"] = np.where(pl > 0, macro["Valor Atual"] / pl, 0)
    macro["Diferença"] = macro["Valor Ideal"] - macro["Valor Atual"]
    macro["Status"] = macro.apply(lambda r: status_por_diff(r["Diferença"], r["Valor Ideal"]), axis=1)
    macro["Ação"] = macro["Diferença"].apply(acao_por_diff)
    macro = macro[["Classe", "Status", "Peso Atual", "Peso Ideal", "Valor Atual", "Valor Ideal", "Diferença", "Ação"]]

    actual_sub = pos_cliente.groupby(["classe_macro", "subbucket"], as_index=False)["valor_mercado"].sum()
    actual_sub = actual_sub.rename(columns={"classe_macro": "Classe", "subbucket": "Subbucket", "valor_mercado": "Valor Atual"})
    sub = actual_sub.merge(sub_target, how="outer", on=["Classe", "Subbucket"]).fillna(0)
    sub["Peso Atual"] = np.where(pl > 0, sub["Valor Atual"] / pl, 0)
    sub["Diferença"] = sub["Valor Ideal"] - sub["Valor Atual"]
    sub["Status"] = sub.apply(lambda r: status_por_diff(r["Diferença"], r["Valor Ideal"]), axis=1)
    sub["Prioridade"] = sub["Diferença"].apply(lambda x: prioridade_por_diff(x, pl))
    sub["Ação"] = sub["Diferença"].apply(acao_por_diff)
    order = {b: i for i, b in enumerate(SUBBUCKET_ORDER)}
    sub["_ord"] = sub["Subbucket"].map(order).fillna(999)
    sub = sub.sort_values(["Classe", "_ord", "Subbucket"]).drop(columns="_ord")
    sub = sub[["Classe", "Subbucket", "Status", "Prioridade", "Peso Atual", "Peso Ideal", "Valor Atual", "Valor Ideal", "Diferença", "Ação"]]
    return macro, sub


def rv_recommendation(pos_cliente: pd.DataFrame, p: dict[str, float], pl: float, modelo: str) -> pd.DataFrame:
    rows = []
    for bucket, tickers in rv_universe(modelo).items():
        alvo_total = pl * peso_get(p, bucket)
        alvo_ativo = alvo_total / len(tickers) if tickers else 0
        for t in tickers:
            mask = pos_cliente["ticker_norm"].eq(ticker_clean(t))
            atual = float(pos_cliente.loc[mask, "valor_mercado"].sum())
            qtd = float(pos_cliente.loc[mask, "quantidade"].sum())
            diff = alvo_ativo - atual
            rows.append([bucket, t, qtd, atual, alvo_ativo, diff, status_por_diff(diff, alvo_ativo), acao_por_diff(diff)])
    return pd.DataFrame(rows, columns=["Subbucket", "Ativo", "Qtd Atual", "Valor Atual", "Valor Ideal", "Diferença", "Status", "Ação"])


def fiinfra_recommendation(pos_cliente: pd.DataFrame, p: dict[str, float], pl: float) -> pd.DataFrame:
    rows = []
    grupos = [
        ("Infraestrutura pós-fixada", peso_get(p, "FiInfra e Cetipados"), FI_INFRA_POS_TICKERS),
        ("Infraestrutura indexada à inflação", peso_get(p, "FiInfra e Cetipado"), FI_INFRA_INFLACAO_TICKERS),
    ]
    for nome, peso_total, tickers in grupos:
        alvo_total = pl * peso_total
        if alvo_total <= 0 or not tickers:
            continue
        alvo_ativo = alvo_total / len(tickers)
        for t in tickers:
            mask = pos_cliente["ticker_norm"].eq(ticker_clean(t))
            atual = float(pos_cliente.loc[mask, "valor_mercado"].sum())
            qtd = float(pos_cliente.loc[mask, "quantidade"].sum())
            diff = alvo_ativo - atual
            rows.append([nome, t, qtd, atual, alvo_ativo, diff, status_por_diff(diff, alvo_ativo), acao_por_diff(diff)])
    return pd.DataFrame(rows, columns=["Estratégia", "Ativo", "Qtd Atual", "Valor Atual", "Valor Ideal", "Diferença", "Status", "Ação"])


def action_summary(pos_cliente: pd.DataFrame, sub_df: pd.DataFrame, pl: float) -> tuple[pd.DataFrame, float, float]:
    saldo = float(pos_cliente.loc[pos_cliente["subbucket"].eq("Saldo em Conta"), "valor_mercado"].sum())
    fora_liquidez = float(pos_cliente.loc[pos_cliente["classe_macro"].eq("Fora da Estratégia"), "valor_mercado"].sum())
    compras = sub_df[(sub_df["Diferença"] > 300) & (~sub_df["Classe"].isin(["Caixa", "Fora da Estratégia"]))].copy()
    vendas = sub_df[(sub_df["Diferença"] < -300) & (~sub_df["Classe"].isin(["Caixa"]))].copy()
    rows = []
    for _, r in vendas.sort_values("Diferença").head(8).iterrows():
        rows.append(["Liberar", r["Subbucket"], r["Classe"], abs(float(r["Diferença"])), "Reduzir excesso"])
    for _, r in compras.sort_values("Diferença", ascending=False).head(8).iterrows():
        rows.append(["Alocar", r["Subbucket"], r["Classe"], float(r["Diferença"]), "Comprar/aportar"])
    acao = pd.DataFrame(rows, columns=["Tipo", "Destino/Origem", "Classe", "Valor", "O que fazer"])
    return acao, saldo, fora_liquidez



PARENT_WEIGHT_KEYS = {
    "RF POS", "RF PÓS", "FUNDOS DE INVEST.", "FUNDOS DE INVEST", "RF PRE", "RF PRÉ",
    "RF INFLACAO", "RF INFLAÇÃO", "RV BRASIL", "INTERNACIONAL"
}

GROUP_DESCRIPTIONS = {
    "Renda fixa pós-fixada": "Parcela voltada para liquidez, estabilidade e acompanhamento das taxas de juros. Em geral, utiliza instrumentos atrelados ao CDI ou à Selic, distribuídos por prazo de resgate.",
    "Renda fixa prefixada": "Parcela com taxa conhecida no momento da aplicação. Pode ajudar a travar juros quando o cenário for favorável, respeitando o prazo e o perfil do cliente.",
    "Renda fixa indexada à inflação": "Parcela que busca preservar poder de compra ao longo do tempo, combinando uma taxa real com a variação da inflação.",
    "Fundos de infraestrutura e crédito incentivado": "Parcela destinada a instrumentos ligados a infraestrutura e crédito incentivado, com potencial de diversificação e geração de renda.",
    "Crédito privado": "Parcela destinada a títulos emitidos por empresas ou estruturas de crédito, buscando retorno adicional mediante análise de risco.",
    "Ações brasileiras": "Parcela de crescimento da carteira, composta por empresas listadas na bolsa brasileira conforme a estratégia definida pela gestão.",
    "Fundos imobiliários": "Parcela de renda variável voltada ao mercado imobiliário, com potencial de geração de renda e diversificação setorial.",
    "Investimentos internacionais": "Parcela voltada à diversificação geográfica e cambial, reduzindo a dependência exclusiva do mercado brasileiro.",
    "Outros instrumentos": "Parcela complementar para instrumentos específicos, quando aplicável à estratégia selecionada.",
}


def friendly_group_for_weight(key: str) -> tuple[str | None, str | None]:
    k = norm(key)
    if k in PARENT_WEIGHT_KEYS:
        return None, None
    if k == "IMEDIATO":
        return "Renda fixa pós-fixada", "Liquidez imediata"
    if "1 A 30" in k:
        return "Renda fixa pós-fixada", "Resgate entre 1 e 30 dias"
    if "31 A 180" in k:
        return "Renda fixa pós-fixada", "Resgate entre 31 e 180 dias"
    if "181 A 360" in k:
        return "Renda fixa pós-fixada", "Resgate entre 181 e 360 dias"
    if "361" in k:
        return "Renda fixa pós-fixada", "Resgate acima de 361 dias"
    if "BANCARIO PRE" in k or "BANCÁRIO PRÉ" in k or "BANCARIO PRÉ" in k:
        return "Renda fixa prefixada", "Títulos bancários prefixados"
    if "TESOURO PRE" in k or "TESOURO PRÉ" in k:
        return "Renda fixa prefixada", "Tesouro prefixado"
    if k == "BANCARIO" or k == "BANCÁRIO":
        return "Renda fixa indexada à inflação", "Títulos bancários indexados à inflação"
    if k == "TESOURO":
        return "Renda fixa indexada à inflação", "Tesouro indexado à inflação"
    if "FIINFRA" in k or "CETIP" in k:
        # Na planilha existem duas alocações estratégicas diferentes.
        # Mantemos as duas separadas para não duplicar a carteira no PDF/teórica.
        if "CETIPADOS" in k:
            return "Fundos de infraestrutura e crédito incentivado", "Infraestrutura pós-fixada"
        if "CETIPADO" in k:
            return "Fundos de infraestrutura e crédito incentivado", "Infraestrutura indexada à inflação"
        if "POS" in k or "PÓS" in k:
            return "Fundos de infraestrutura e crédito incentivado", "Infraestrutura pós-fixada"
        if "INFL" in k or "IPCA" in k:
            return "Fundos de infraestrutura e crédito incentivado", "Infraestrutura indexada à inflação"
        return "Fundos de infraestrutura e crédito incentivado", "Fundos de infraestrutura e crédito incentivado"
    if "CREDITO PRIVADO" in k or "CRÉDITO PRIVADO" in k:
        return "Crédito privado", "Crédito privado"
    if "ACOES" in k or "AÇÕES" in k or k == "ACOES" or k == "AÇÕES":
        return "Ações brasileiras", "Carteira de ações brasileiras"
    if "FII" in k:
        return "Fundos imobiliários", "Fundos imobiliários"
    if k == "RENDA FIXA":
        return "Investimentos internacionais", "Renda fixa internacional"
    if k == "RENDA VARIAVEL" or k == "RENDA VARIÁVEL":
        return "Investimentos internacionais", "Renda variável internacional"
    return "Outros instrumentos", str(key).strip()


def component_explanation(group: str, component: str) -> str:
    if group == "Renda fixa pós-fixada":
        return "Componente de liquidez e estabilidade, organizado conforme o prazo de disponibilidade dos recursos."
    if group == "Renda fixa prefixada":
        return "Componente com taxa definida no início da aplicação."
    if group == "Renda fixa indexada à inflação":
        return "Componente voltado à proteção de poder de compra."
    if group == "Fundos de infraestrutura e crédito incentivado":
        if "pós" in component.lower():
            return "Parcela voltada a instrumentos de infraestrutura com comportamento mais próximo ao CDI e foco em geração de renda."
        if "inflação" in component.lower():
            return "Parcela voltada a instrumentos de infraestrutura indexados à inflação, buscando proteção de poder de compra."
        return "Seleção estratégica de instrumentos ligados a infraestrutura e crédito incentivado."
    if group == "Ações brasileiras":
        return "Carteira estratégica de empresas brasileiras definida pela gestão."
    if group == "Fundos imobiliários":
        return "Carteira estratégica de fundos imobiliários definida pela gestão."
    if group == "Investimentos internacionais":
        return "Exposição internacional para diversificação geográfica e cambial."
    return "Componente complementar da estratégia."


def display_order_group(group: str) -> int:
    order = [
        "Renda fixa pós-fixada", "Renda fixa prefixada", "Renda fixa indexada à inflação",
        "Fundos de infraestrutura e crédito incentivado", "Crédito privado", "Ações brasileiras",
        "Fundos imobiliários", "Investimentos internacionais", "Outros instrumentos"
    ]
    return order.index(group) if group in order else 999


def fiinfra_tickers_for_component(component: str) -> list[str]:
    c = norm(component)
    if "POS" in c or "PÓS" in c:
        return FI_INFRA_POS_TICKERS
    if "INFL" in c or "IPCA" in c:
        return FI_INFRA_INFLACAO_TICKERS
    return FI_INFRA_TICKERS


def theoretical_portfolio(p: dict[str, float], valor: float, modelo: str) -> pd.DataFrame:
    rows = []
    for key, weight in p.items():
        w = float(weight or 0)
        if w <= 0:
            continue
        group, component = friendly_group_for_weight(key)
        if not group or not component:
            continue
        rows.append({
            "Grupo": group,
            "Composição": component,
            "Nível": "Composição",
            "Ativo": "",
            "Peso": w,
            "Valor": valor * w,
            "Explicação": component_explanation(group, component),
        })

        if group == "Ações brasileiras":
            tickers = rv_universe(modelo).get("Ações", [])
            for t in tickers:
                rows.append({"Grupo": group, "Composição": component, "Nível": "Ativo", "Ativo": t, "Peso": w / len(tickers) if tickers else 0, "Valor": valor * w / len(tickers) if tickers else 0, "Explicação": "Empresa brasileira da carteira estratégica."})
        elif group == "Fundos imobiliários":
            tickers = rv_universe(modelo).get("FIIs", [])
            for t in tickers:
                rows.append({"Grupo": group, "Composição": component, "Nível": "Ativo", "Ativo": t, "Peso": w / len(tickers) if tickers else 0, "Valor": valor * w / len(tickers) if tickers else 0, "Explicação": "Fundo imobiliário da carteira estratégica."})
        elif group == "Fundos de infraestrutura e crédito incentivado":
            tickers = fiinfra_tickers_for_component(component)
            for t in tickers:
                rows.append({"Grupo": group, "Composição": component, "Nível": "Ativo", "Ativo": t, "Peso": w / len(tickers) if tickers else 0, "Valor": valor * w / len(tickers) if tickers else 0, "Explicação": "Instrumento selecionado pela estratégia de infraestrutura e crédito incentivado."})

    df = pd.DataFrame(rows, columns=["Grupo", "Composição", "Nível", "Ativo", "Peso", "Valor", "Explicação"])
    if df.empty:
        return df
    df["_ord"] = df["Grupo"].apply(display_order_group)
    df["_nivel_ord"] = df["Nível"].map({"Composição": 0, "Ativo": 1}).fillna(9)
    return df.sort_values(["_ord", "Composição", "_nivel_ord", "Ativo"]).drop(columns=["_ord", "_nivel_ord"]).reset_index(drop=True)


def portfolio_macro_cliente(df_teor: pd.DataFrame) -> pd.DataFrame:
    base = df_teor[df_teor["Nível"].eq("Composição")].copy()
    if base.empty:
        return pd.DataFrame(columns=["Classe de investimento", "Peso sugerido", "Valor sugerido"])
    macro = base.groupby("Grupo", as_index=False).agg(Peso=("Peso", "sum"), Valor=("Valor", "sum"))
    macro["_ord"] = macro["Grupo"].apply(display_order_group)
    macro = macro.sort_values("_ord").drop(columns="_ord")
    return macro.rename(columns={"Grupo": "Classe de investimento", "Peso": "Peso sugerido", "Valor": "Valor sugerido"})


def pdf_paragraph(value, style_name="Cell", font_size=7.1, bold=False, color="#222222", align=0) -> Paragraph:
    """Cria células com quebra de linha real no ReportLab, evitando texto sobreposto no PDF."""
    font = PDF_FONT_BOLD if bold else PDF_FONT_REGULAR
    txt = escape("" if value is None else str(value)).replace("\n", "<br/>")
    return Paragraph(
        txt,
        ParagraphStyle(
            name=style_name,
            fontName=font,
            fontSize=font_size,
            leading=font_size + 2.2,
            textColor=colors.HexColor(color),
            alignment=align,
            wordWrap="CJK",
        ),
    )


def table_for_pdf(rows: list[list[str]], col_widths: list[float], header_bg="#172b4d", font_size=7.1, numeric_cols: list[int] | None = None) -> Table:
    """Tabela segura para PDF: todas as células são Paragraph, com wrap e padding."""
    numeric_cols = numeric_cols or []
    wrapped = []
    for ri, row in enumerate(rows):
        new_row = []
        for ci, cell in enumerate(row):
            is_header = ri == 0
            align = 1 if is_header else (2 if ci in numeric_cols else 0)
            new_row.append(
                pdf_paragraph(
                    cell,
                    style_name=f"PDFTable_{ri}_{ci}",
                    font_size=font_size if not is_header else max(font_size, 7.2),
                    bold=is_header,
                    color="#FFFFFF" if is_header else "#222222",
                    align=align,
                )
            )
        wrapped.append(new_row)

    tbl = Table(wrapped, colWidths=[w * cm for w in col_widths], repeatRows=1, splitByRow=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor(header_bg)),
        ("GRID", (0,0), (-1,-1), .2, colors.HexColor("#d9dde5")),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("RIGHTPADDING", (0,0), (-1,-1), 5),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f7f8fb")]),
    ]))
    return tbl


def build_pdf_teorico(df_teor: pd.DataFrame, modelo: str, valor: float, cliente: str = "") -> BytesIO:
    if not HAS_REPORTLAB:
        raise RuntimeError("ReportLab não está instalado.")
    register_pdf_fonts()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=1.25 * cm, leftMargin=1.25 * cm, topMargin=1.0 * cm, bottomMargin=1.0 * cm)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="MWTitle", parent=styles["Title"], fontName=PDF_FONT_BOLD, fontSize=18, alignment=TA_CENTER, leading=22, textColor=colors.HexColor("#111111")))
    styles.add(ParagraphStyle(name="MWSub", parent=styles["Normal"], fontName=PDF_FONT_REGULAR, fontSize=9, alignment=TA_CENTER, textColor=colors.HexColor("#555555")))
    styles.add(ParagraphStyle(name="MWSection", parent=styles["Heading2"], fontName=PDF_FONT_BOLD, fontSize=12, leading=15, textColor=colors.HexColor("#172b4d"), spaceBefore=8, spaceAfter=4))
    styles.add(ParagraphStyle(name="MWText", parent=styles["Normal"], fontName=PDF_FONT_REGULAR, fontSize=8.2, leading=10.5, textColor=colors.HexColor("#333333")))
    styles.add(ParagraphStyle(name="MWSmall", parent=styles["Normal"], fontName=PDF_FONT_REGULAR, fontSize=7.2, leading=9, textColor=colors.HexColor("#555555")))
    story = []
    lp = logo_pdf_path()
    if lp:
        story.append(Image(str(lp), width=5.8 * cm, height=1.8 * cm, kind="proportional"))
        story.append(Spacer(1, .2 * cm))
    story.append(Paragraph("Estudo de Alocação Teórica", styles["MWTitle"]))
    story.append(Paragraph("Simulação ilustrativa de carteira por perfil de investimento", styles["MWSub"]))
    story.append(Spacer(1, .4 * cm))

    info = [["Cliente", cliente.strip() or "Não informado"], ["Perfil utilizado", modelo], ["Valor simulado", format_brl(valor)], ["Data", datetime.now().strftime("%d/%m/%Y")]]
    story.append(Table(info, colWidths=[4.0 * cm, 12.5 * cm], style=[
        ("GRID", (0,0), (-1,-1), .25, colors.HexColor("#d9dde5")),
        ("BACKGROUND", (0,0), (0,-1), colors.HexColor("#eef1f6")),
        ("FONTNAME", (0,0), (0,-1), PDF_FONT_BOLD),
        ("FONTSIZE", (0,0), (-1,-1), 8),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
    ]))
    story.append(Spacer(1, .35 * cm))

    macro = portfolio_macro_cliente(df_teor)
    data = [["Classe de investimento", "Peso sugerido", "Valor sugerido"]]
    for _, r in macro.iterrows():
        data.append([r["Classe de investimento"], fmt_pct(r["Peso sugerido"]), format_brl(r["Valor sugerido"])])
    story.append(Paragraph("Resumo da alocação sugerida", styles["MWSection"]))
    story.append(table_for_pdf(data, [8.2, 3.0, 4.0], font_size=7.8, numeric_cols=[1, 2]))
    story.append(Spacer(1, .25 * cm))

    for group in macro["Classe de investimento"].tolist():
        comp = df_teor[(df_teor["Grupo"].eq(group)) & (df_teor["Nível"].eq("Composição"))].copy()
        ativos = df_teor[(df_teor["Grupo"].eq(group)) & (df_teor["Nível"].eq("Ativo"))].copy()
        story.append(Paragraph(group, styles["MWSection"]))
        story.append(Paragraph(GROUP_DESCRIPTIONS.get(group, "Componente da estratégia de alocação."), styles["MWText"]))
        story.append(Spacer(1, .12 * cm))
        data = [["Composição", "Peso", "Valor", "Explicação"]]
        for _, r in comp.iterrows():
            data.append([r["Composição"], fmt_pct(r["Peso"]), format_brl(r["Valor"]), r["Explicação"]])
        story.append(table_for_pdf(data, [4.5, 2.1, 3.1, 7.0], font_size=6.9, numeric_cols=[1, 2]))
        if not ativos.empty:
            story.append(Spacer(1, .12 * cm))
            for component_name, ativos_component in ativos.groupby("Composição", sort=False):
                if len(ativos["Composição"].dropna().unique()) > 1:
                    story.append(Paragraph(f"Ativos — {component_name}", styles["MWSmall"]))
                    story.append(Spacer(1, .06 * cm))
                data = [["Ativo", "Peso", "Valor"]]
                for _, r in ativos_component.iterrows():
                    data.append([r["Ativo"], fmt_pct(r["Peso"]), format_brl(r["Valor"])])
                story.append(table_for_pdf(data, [6.0, 3.0, 4.0], font_size=7.0, numeric_cols=[1, 2]))
                story.append(Spacer(1, .10 * cm))
        story.append(Spacer(1, .18 * cm))

    story.append(Spacer(1, .3 * cm))
    story.append(Paragraph("Disclaimer", styles["MWSection"]))
    story.append(Paragraph("Este material é meramente informativo e apresenta uma simulação baseada em parâmetros internos de alocação. A composição final da carteira depende da análise individual do investidor, de sua política de suitability, disponibilidade de produtos, condições de mercado e avaliação da equipe responsável. Rentabilidade passada não representa garantia de rentabilidade futura.", styles["MWSmall"]))
    doc.build(story)
    buf.seek(0)
    return buf


# =============================================================================
# Layout global
# =============================================================================
pesos = load_pesos_xlsx(str(find_file("Pesos-alocacao.xlsx")))
df_contas = load_contas_cached()

with st.sidebar:
    lp = logo_path()
    if lp:
        st.image(str(lp), use_container_width=True)
    st.caption(f"Versão {APP_VERSION}")
    page = st.radio("Navegação", ["Controle de Saldo", "Asset Allocation", "Carteira Teórica"], index=0)

st.title("M Wealth - Balanceamento de Carteiras")

# =============================================================================
# Página 1 - Controle de saldo
# =============================================================================
if page == "Controle de Saldo":
    st.header("Controle de Saldo para Operação")
    st.markdown('<div class="mw-muted">Tela enxuta para identificar contas com saldo financeiro disponível para operação.</div>', unsafe_allow_html=True)
    c0, c1, c2 = st.columns([1.2, 1.3, 3.5])
    force = c0.button("Atualizar base", type="primary")
    if force:
        st.cache_data.clear()
    min_saldo = c1.number_input("Saldo mínimo", min_value=0.0, value=1000.0, step=1000.0, format="%.2f")

    try:
        df_latest, meta, mode = load_positions_cached(force_rebuild=force)
        df_latest = enrich_positions_cached(df_latest)
    except Exception as e:
        st.error(f"Falha ao carregar/consolidar posições: {e}")
        st.stop()

    st.caption(f"Última consolidação: **{meta.get('built_at', 'n/d')}**")

    if "saldo_operacional" not in df_latest.columns:
        df_latest["saldo_operacional"] = False
    df_latest["saldo_operacional"] = df_latest["saldo_operacional"].fillna(False).astype(bool)

    saldo_conta = (
        df_latest[df_latest["saldo_operacional"]]
        .groupby(["GRUPO GERAL", "CLIENTE", "corretora", "conta"], dropna=False, as_index=False)["valor_mercado"]
        .sum()
        .rename(columns={"valor_mercado": "Saldo"})
    )
    pl_conta = (
        df_latest.groupby(["GRUPO GERAL", "CLIENTE", "corretora", "conta"], dropna=False, as_index=False)["valor_mercado"]
        .sum()
        .rename(columns={"valor_mercado": "PL"})
    )
    painel = pl_conta.merge(saldo_conta, how="left", on=["GRUPO GERAL", "CLIENTE", "corretora", "conta"]).fillna({"Saldo": 0.0})

    operaveis = painel[(painel["Saldo"] >= min_saldo) | (painel["Saldo"] < 0)].sort_values("Saldo", ascending=False)
    k1, k2, k3 = st.columns(3)
    k1.metric("Contas com saldo ≥ mínimo", int((painel["Saldo"] >= min_saldo).sum()))
    k2.metric("Saldo total disponível", format_brl(painel.loc[painel["Saldo"] > 0, "Saldo"].sum()))
    k3.metric("Caixa negativo", format_brl(painel.loc[painel["Saldo"] < 0, "Saldo"].sum()))

    st.subheader("Contas com saldo para operação")
    cols = ["GRUPO GERAL", "CLIENTE", "corretora", "conta", "PL", "Saldo"]
    st.dataframe(prepare_display(operaveis[cols], money_cols=["PL", "Saldo"], max_rows=700), use_container_width=True, hide_index=True)

    with st.expander("Ver todas as contas, inclusive sem saldo operacional", expanded=False):
        st.dataframe(prepare_display(painel[cols].sort_values("Saldo", ascending=False), money_cols=["PL", "Saldo"], max_rows=1200), use_container_width=True, hide_index=True)


# =============================================================================
# Página 2 - Asset Allocation
# =============================================================================
if page == "Asset Allocation":
    st.header("Asset Allocation - Cliente / Grupo Familiar")
    try:
        df_latest, meta, mode = load_positions_cached(force_rebuild=False)
        df_latest = enrich_positions_cached(df_latest)
    except Exception as e:
        st.error(f"Não consegui carregar a base: {e}")
        st.stop()

    if "GRUPO GERAL" not in df_latest.columns:
        st.error("A base não possui a coluna GRUPO GERAL. Verifique o arquivo Contas.xlsx.")
        st.stop()
    if not pesos:
        st.error("Pesos-alocacao.xlsx não foi encontrado ou não pôde ser lido.")
        st.stop()

    grupos = sorted(df_latest["GRUPO GERAL"].dropna().astype(str).unique())
    col_g, col_c, col_m = st.columns([3, 3, 2])
    with col_g:
        grupo_sel = st.selectbox("Grupo familiar", grupos)
    contas_info = df_latest[df_latest["GRUPO GERAL"].astype(str).eq(str(grupo_sel))][["conta", "CLIENTE", "corretora"]].drop_duplicates()
    with col_c:
        opcoes = ["Todas as contas"] + [f"{str(r.CLIENTE).strip()} • {r.corretora} ({r.conta})" if pd.notna(r.CLIENTE) else f"{r.corretora} ({r.conta})" for r in contas_info.itertuples(index=False)]
        conta_sel = st.selectbox("Conta", opcoes)
    with col_m:
        perfil_cliente = "Não identificado"
        if not df_contas.empty and "GRUPO GERAL" in df_contas.columns:
            m = df_contas[df_contas["GRUPO GERAL"].astype(str).str.strip().eq(str(grupo_sel).strip())]
            if not m.empty and "Perfil Carteira" in m.columns:
                perfil_cliente = str(m["Perfil Carteira"].iloc[0]).strip()
        modelos = list(pesos.keys())
        idx = modelos.index(model_for_profile(perfil_cliente, modelos)) if model_for_profile(perfil_cliente, modelos) in modelos else 0
        modelo = st.selectbox("Modelo de alocação", modelos, index=idx)

    pos_cliente = df_latest[df_latest["GRUPO GERAL"].astype(str).eq(str(grupo_sel))].copy()
    if conta_sel != "Todas as contas":
        conta_real = conta_sel.split("(")[-1].strip(")")
        pos_cliente = pos_cliente[pos_cliente["conta"].astype(str).eq(conta_real)].copy()

    p = pesos[modelo]
    pl = float(pos_cliente["valor_mercado"].sum())
    macro_df, sub_df = portfolio_tables(pos_cliente, p, pl)
    acoes_df, saldo_disponivel, fora_liquidez = action_summary(pos_cliente, sub_df, pl)

    pl_xp = float(pos_cliente.loc[pos_cliente["corretora"].eq("XP"), "valor_mercado"].sum())
    pl_btg = float(pos_cliente.loc[pos_cliente["corretora"].eq("BTG"), "valor_mercado"].sum())
    pl_cs = float(pos_cliente.loc[pos_cliente["corretora"].eq("CS"), "valor_mercado"].sum())
    saldo = float(pos_cliente.loc[pos_cliente["subbucket"].eq("Saldo em Conta"), "valor_mercado"].sum())
    nao_class = float(pos_cliente.loc[pos_cliente["subbucket"].eq("Outros / Não Classificado"), "valor_mercado"].sum())

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("PL Total", format_brl(pl), delta=f"{perfil_cliente}")
    k2.metric("XP", format_brl(pl_xp))
    k3.metric("BTG", format_brl(pl_btg))
    k4.metric("CS", format_brl(pl_cs))
    k5.metric("Saldo", format_brl(saldo))

    st.markdown('<div class="mw-line"></div>', unsafe_allow_html=True)
    st.subheader("1. O que precisa ser feito")
    c1, c2, c3 = st.columns(3)
    c1.metric("Caixa disponível", format_brl(saldo_disponivel))
    c2.metric("Fora da estratégia", format_brl(fora_liquidez))
    c3.metric("Não classificado", format_brl(nao_class))
    if acoes_df.empty:
        st.success("Carteira próxima do modelo dentro da tolerância operacional.")
    else:
        st.dataframe(prepare_display(acoes_df, money_cols=["Valor"]), use_container_width=True, hide_index=True)

    st.subheader("2. Visão macro atual x ideal")
    col1, col2 = st.columns([1.05, 1.95])
    with col1:
        plot = macro_df[macro_df["Valor Atual"] > 0].copy()
        fig = px.pie(plot, names="Classe", values="Valor Atual", title="Atual", hole=.48)
        fig.update_layout(height=295, margin=dict(l=8, r=8, t=45, b=8), showlegend=True)
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        st.dataframe(prepare_display(macro_df, money_cols=["Valor Atual", "Valor Ideal", "Diferença"], pct_cols=["Peso Atual", "Peso Ideal"]), use_container_width=True, hide_index=True)

    st.subheader("3. Alocação por subbucket")
    filtro = st.segmented_control("Filtro", ["Todos", "Só ações necessárias", "Excessos", "Falta comprar"], default="Só ações necessárias")
    sub_view = sub_df.copy()
    if filtro == "Só ações necessárias":
        sub_view = sub_view[sub_view["Ação"].ne("Manter / OK")]
    elif filtro == "Excessos":
        sub_view = sub_view[sub_view["Diferença"] < -300]
    elif filtro == "Falta comprar":
        sub_view = sub_view[sub_view["Diferença"] > 300]
    st.dataframe(prepare_display(sub_view, money_cols=["Valor Atual", "Valor Ideal", "Diferença"], pct_cols=["Peso Atual", "Peso Ideal"], max_rows=300), use_container_width=True, hide_index=True)

    st.subheader("4. Sugestão por ativo - RV Brasil e FiInfra")
    rv_df = rv_recommendation(pos_cliente, p, pl, modelo)
    fi_df = fiinfra_recommendation(pos_cliente, p, pl)
    tab_a, tab_b = st.tabs(["Ações e FIIs", "FiInfra"])
    with tab_a:
        rv_view = rv_df[rv_df["Valor Ideal"].gt(0)].copy()
        st.dataframe(prepare_display(rv_view, money_cols=["Valor Atual", "Valor Ideal", "Diferença"], qty_cols=["Qtd Atual"]), use_container_width=True, hide_index=True)
    with tab_b:
        fi_view = fi_df[fi_df["Valor Ideal"].gt(0)].copy()
        if fi_view.empty:
            st.info("Este modelo não possui alvo para FiInfra.")
        else:
            st.dataframe(prepare_display(fi_view, money_cols=["Valor Atual", "Valor Ideal", "Diferença"], qty_cols=["Qtd Atual"]), use_container_width=True, hide_index=True)

    st.subheader("5. Detalhamento das posições")
    buckets = [b for b in SUBBUCKET_ORDER if b in set(pos_cliente["subbucket"])]
    bucket_sel = st.selectbox("Abrir posições por subbucket", buckets if buckets else ["Nenhum"])
    if bucket_sel != "Nenhum":
        subpos = pos_cliente[pos_cliente["subbucket"].eq(bucket_sel)].copy()
        agrup = subpos.groupby(["asset_id", "asset_nome", "corretora", "subbucket"], dropna=False, as_index=False).agg(Valor=("valor_mercado", "sum"), Quantidade=("quantidade", "sum"), Contas=("conta", "nunique")).sort_values("Valor", ascending=False)
        agrup["Peso no Cliente"] = np.where(pl > 0, agrup["Valor"] / pl, 0)
        st.dataframe(prepare_display(agrup, money_cols=["Valor"], pct_cols=["Peso no Cliente"], qty_cols=["Quantidade"], max_rows=600), use_container_width=True, hide_index=True)

    with st.expander("Revisões e exceções", expanded=False):
        universo = set(ACOES_SEM_RENDA + ACOES_COM_RENDA + FIIS_RECOMENDADOS + FI_INFRA_TICKERS)
        fora_df = pos_cliente[
            pos_cliente["classe_macro"].eq("Fora da Estratégia") |
            ((pos_cliente["classe_macro"].eq("RV Brasil")) & (~pos_cliente["ticker_norm"].isin({ticker_clean(x) for x in universo}))) |
            (pos_cliente["subbucket"].str.contains("Sem Liquidez|Não Classificado|COE|Previdência", case=False, na=False))
        ].sort_values("valor_mercado", ascending=False)
        cols = ["corretora", "conta", "CLIENTE", "asset_id", "asset_nome", "classe_macro", "subbucket", "tratamento", "valor_mercado", "quantidade", "indexador", "liquidez", "vencimento"]
        st.dataframe(prepare_display(fora_df[[c for c in cols if c in fora_df.columns]], money_cols=["valor_mercado"], qty_cols=["quantidade"], max_rows=500), use_container_width=True, hide_index=True)


# =============================================================================
# Página 3 - Carteira Teórica
# =============================================================================
if page == "Carteira Teórica":
    st.header("Carteira Teórica - Simulador para Cliente")
    st.markdown(
        '<div class="mw-muted">Visualização da carteira modelo de forma simples, organizada e apresentável para o cliente final.</div>',
        unsafe_allow_html=True,
    )
    modelos = list(pesos.keys())
    if not modelos:
        st.error("Pesos-alocacao.xlsx não foi encontrado ou não pôde ser lido.")
        st.stop()

    c1, c2, c3 = st.columns([2, 1.2, 2])
    with c1:
        modelo = st.selectbox("Perfil de carteira", modelos)
    with c2:
        valor = st.number_input("Valor simulado", min_value=0.0, value=1_000_000.0, step=100_000.0, format="%.2f")
    with c3:
        cliente = st.text_input("Nome do cliente no PDF (opcional)")

    df_teor = theoretical_portfolio(pesos[modelo], valor, modelo)
    if df_teor.empty:
        st.warning("Não encontrei componentes válidos para essa carteira. Verifique a planilha de pesos.")
        st.stop()

    macro = portfolio_macro_cliente(df_teor)
    renda_fixa = macro[macro["Classe de investimento"].str.contains("Renda fixa|infraestrutura|Crédito privado", case=False, na=False)]["Valor sugerido"].sum()
    renda_variavel = macro[macro["Classe de investimento"].str.contains("Ações|Fundos imobiliários", case=False, na=False)]["Valor sugerido"].sum()
    internacional = macro[macro["Classe de investimento"].str.contains("internacionais", case=False, na=False)]["Valor sugerido"].sum()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Valor simulado", format_brl(valor))
    k2.metric("Renda fixa no Brasil", format_brl(renda_fixa))
    k3.metric("Renda variável no Brasil", format_brl(renda_variavel))
    k4.metric("Investimentos internacionais", format_brl(internacional))

    col_a, col_b = st.columns([1.05, 1.2])
    with col_a:
        fig = px.pie(macro, names="Classe de investimento", values="Valor sugerido", title="Distribuição sugerida", hole=.48)
        fig.update_layout(height=330, margin=dict(l=8, r=8, t=45, b=8), legend_title_text="")
        st.plotly_chart(fig, use_container_width=True)
    with col_b:
        st.subheader("Resumo da alocação")
        st.dataframe(
            prepare_display(macro, money_cols=["Valor sugerido"], pct_cols=["Peso sugerido"]),
            use_container_width=True,
            hide_index=True,
        )

    st.markdown('<div class="mw-line"></div>', unsafe_allow_html=True)
    st.subheader("Composição por classe de investimento")

    for group in macro["Classe de investimento"].tolist():
        comp = df_teor[(df_teor["Grupo"].eq(group)) & (df_teor["Nível"].eq("Composição"))].copy()
        ativos = df_teor[(df_teor["Grupo"].eq(group)) & (df_teor["Nível"].eq("Ativo"))].copy()
        total_group = float(comp["Valor"].sum())
        peso_group = float(comp["Peso"].sum())
        with st.expander(f"{group} — {fmt_pct(peso_group)} | {format_brl(total_group)}", expanded=True):
            st.markdown(f'<div class="mw-muted">{GROUP_DESCRIPTIONS.get(group, "Componente da estratégia de alocação.")}</div>', unsafe_allow_html=True)
            tabela_comp = comp.rename(columns={"Peso": "Peso sugerido", "Valor": "Valor sugerido"})[["Composição", "Peso sugerido", "Valor sugerido", "Explicação"]]
            st.dataframe(
                prepare_display(tabela_comp, money_cols=["Valor sugerido"], pct_cols=["Peso sugerido"]),
                use_container_width=True,
                hide_index=True,
            )
            if not ativos.empty:
                st.markdown("**Ativos utilizados na carteira modelo**")
                if ativos["Composição"].nunique() > 1:
                    for comp_nome, ativos_comp in ativos.groupby("Composição", sort=False):
                        st.markdown(f"_{comp_nome}_")
                        tabela_ativos = ativos_comp.rename(columns={"Peso": "Peso sugerido", "Valor": "Valor sugerido"})[["Ativo", "Peso sugerido", "Valor sugerido"]]
                        st.dataframe(
                            prepare_display(tabela_ativos, money_cols=["Valor sugerido"], pct_cols=["Peso sugerido"]),
                            use_container_width=True,
                            hide_index=True,
                        )
                else:
                    tabela_ativos = ativos.rename(columns={"Peso": "Peso sugerido", "Valor": "Valor sugerido"})[["Ativo", "Peso sugerido", "Valor sugerido"]]
                    st.dataframe(
                        prepare_display(tabela_ativos, money_cols=["Valor sugerido"], pct_cols=["Peso sugerido"]),
                        use_container_width=True,
                        hide_index=True,
                    )

    st.markdown('<div class="mw-line"></div>', unsafe_allow_html=True)
    st.subheader("Gerar material para cliente")
    try:
        pdf = build_pdf_teorico(df_teor, modelo, valor, cliente)
        st.download_button(
            "Baixar PDF da carteira teórica",
            data=pdf,
            file_name=f"carteira_teorica_mwealth_{modelo.lower().replace(' ', '_')}.pdf",
            mime="application/pdf",
            type="primary",
        )
    except Exception as e:
        st.info(f"PDF indisponível: {e}")



st.caption("M Wealth Asset Allocation")
