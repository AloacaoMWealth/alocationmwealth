from __future__ import annotations

import json
import math
import unicodedata
from datetime import datetime
from io import BytesIO
from pathlib import Path

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
    HAS_REPORTLAB = True
except Exception:
    HAS_REPORTLAB = False

st.set_page_config(page_title="M Wealth | Balanceamento", layout="wide", page_icon="📊")

BASE_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
POS_DIR = BASE_DIR / "posicoes"
APP_VERSION = "3.0 operacional"

# Estratégia de RV e FiInfra permanece no código, conforme orientação da gestão.
ACOES_SEM_RENDA = ["AXIA3", "EQTL3", "SBSP3", "ITUB3", "BPAC11", "PSSA3", "PRIO3", "VALE3", "WEGE3", "RENT3"]
ACOES_COM_RENDA = ["CPLE3", "EGIE3", "AXIA3", "ITUB3", "VALE3", "ALOS3", "FLRY3", "ABEV3", "PRIO3", "WEGE3"]
FIIS_RECOMENDADOS = ["KNRI11", "XPML11", "HGLG11", "PVBI11", "HGRU11", "KNCR11", "KNIP11", "KNCA11"]
FI_INFRA_TICKERS = ["KNDI11", "CDII11", "IFRI11", "AZQI11", "KNCE11", "AZIN11", "JURO11", "IFRA11", "KDIF11", "JGPI11", "BDIF11", "JMBI11", "CPTI11"]

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

    # Internacional
    if corretora == "CS":
        if any(x in text for x in ["CASH", "MONEY MARKET", "SWEEP", "BANK DEPOSIT"]):
            return pd.Series(["Internacional", "Caixa Internacional", "Caixa Internacional", "Operacional"])
        if any(x in text for x in ["BOND", "FIXED", "TREASURY", "CD ", "CERTIFICATE", "NOTE", "CORPORATE"]):
            return pd.Series(["Internacional", "Renda Fixa Internacional", "Renda Fixa Internacional", "Estratégia"])
        return pd.Series(["Internacional", "Renda Variável Internacional", "Renda Variável Internacional", "Estratégia"])

    # Caixa / saldo
    if any(x in text for x in ["SALDO FINANCEIRO", "CONTA CORRENTE", "VALORDISPONIVEL", "FINANCEIRO", "CC"]):
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
    alvo_total = pl * (peso_get(p, "FiInfra e Cetipados") + peso_get(p, "FiInfra e Cetipado"))
    alvo_ativo = alvo_total / len(FI_INFRA_TICKERS) if FI_INFRA_TICKERS else 0
    rows = []
    for t in FI_INFRA_TICKERS:
        mask = pos_cliente["ticker_norm"].eq(ticker_clean(t))
        atual = float(pos_cliente.loc[mask, "valor_mercado"].sum())
        qtd = float(pos_cliente.loc[mask, "quantidade"].sum())
        diff = alvo_ativo - atual
        rows.append(["FiInfra e Cetipados", t, qtd, atual, alvo_ativo, diff, status_por_diff(diff, alvo_ativo), acao_por_diff(diff)])
    return pd.DataFrame(rows, columns=["Subbucket", "Ativo", "Qtd Atual", "Valor Atual", "Valor Ideal", "Diferença", "Status", "Ação"])


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


def theoretical_portfolio(p: dict[str, float], valor: float, modelo: str) -> pd.DataFrame:
    rows = []
    sub = subbucket_targets_from_model(p, valor)
    order = {b: i for i, b in enumerate(SUBBUCKET_ORDER)}
    sub["_ord"] = sub["Subbucket"].map(order).fillna(999)
    sub = sub.sort_values(["Classe", "_ord"])
    for _, r in sub.iterrows():
        classe, bucket, w, val = r["Classe"], r["Subbucket"], r["Peso Ideal"], r["Valor Ideal"]
        rows.append([classe, bucket, "Subbucket", "", w, val, ""]) 
        if bucket in ["Ações", "FIIs"]:
            tickers = rv_universe(modelo).get(bucket, [])
            for t in tickers:
                rows.append([classe, bucket, "Ativo", t, w / len(tickers) if tickers else 0, val / len(tickers) if tickers else 0, "Carteira estratégica"])
        if bucket == "FiInfra e Cetipados":
            for t in FI_INFRA_TICKERS:
                rows.append([classe, bucket, "Ativo", t, w / len(FI_INFRA_TICKERS), val / len(FI_INFRA_TICKERS), "Lista estratégica"])
    return pd.DataFrame(rows, columns=["Classe", "Subbucket", "Nível", "Ativo", "Peso", "Valor", "Observação"])


def build_pdf_teorico(df_teor: pd.DataFrame, modelo: str, valor: float, cliente: str = "") -> BytesIO:
    if not HAS_REPORTLAB:
        raise RuntimeError("ReportLab não está instalado.")
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=1.2 * cm, leftMargin=1.2 * cm, topMargin=1 * cm, bottomMargin=1 * cm)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="MWTitle", parent=styles["Title"], fontSize=17, alignment=TA_CENTER, leading=21))
    styles.add(ParagraphStyle(name="MWSmall", parent=styles["Normal"], fontSize=7.5, leading=9))
    story = []
    lp = logo_path()
    if lp:
        story.append(Image(str(lp), width=5 * cm, height=1.6 * cm, kind="proportional"))
        story.append(Spacer(1, .2 * cm))
    story.append(Paragraph("Estudo de Carteira Teórica", styles["MWTitle"]))
    story.append(Paragraph(f"M Wealth • {datetime.now().strftime('%d/%m/%Y')}", styles["Normal"]))
    story.append(Spacer(1, .35 * cm))
    info = [["Modelo", modelo], ["Valor simulado", format_brl(valor)]]
    if cliente.strip():
        info.insert(0, ["Cliente", cliente.strip()])
    story.append(Table(info, colWidths=[4 * cm, 12 * cm], style=[("GRID", (0,0), (-1,-1), .25, colors.grey), ("BACKGROUND", (0,0), (0,-1), colors.HexColor("#eef1f6")), ("FONTSIZE", (0,0), (-1,-1), 8)]))
    story.append(Spacer(1, .35 * cm))
    sub = df_teor[df_teor["Nível"].eq("Subbucket")].copy()
    data = [["Classe", "Subbucket", "Peso", "Valor"]]
    for _, r in sub.iterrows():
        data.append([r["Classe"], r["Subbucket"], fmt_pct(r["Peso"]), format_brl(r["Valor"])])
    tbl = Table(data, colWidths=[3.2 * cm, 7.0 * cm, 2.2 * cm, 4.0 * cm], repeatRows=1)
    tbl.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#172b4d")), ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("FONTSIZE", (0,0), (-1,-1), 7), ("GRID", (0,0), (-1,-1), .2, colors.lightgrey), ("ALIGN", (2,1), (-1,-1), "RIGHT")]))
    story.append(Paragraph("Abertura por Subbucket", styles["Heading2"]))
    story.append(tbl)
    ativos = df_teor[df_teor["Nível"].eq("Ativo")].copy()
    if not ativos.empty:
        story.append(PageBreak())
        story.append(Paragraph("Carteira Teórica por Ativo", styles["Heading2"]))
        data = [["Subbucket", "Ativo", "Peso", "Valor", "Observação"]]
        for _, r in ativos.iterrows():
            data.append([r["Subbucket"], r["Ativo"], fmt_pct(r["Peso"]), format_brl(r["Valor"]), r["Observação"]])
        tbl = Table(data, colWidths=[5.1 * cm, 3.0 * cm, 2.0 * cm, 3.4 * cm, 3.2 * cm], repeatRows=1)
        tbl.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#172b4d")), ("TEXTCOLOR", (0,0), (-1,0), colors.white), ("FONTSIZE", (0,0), (-1,-1), 6.8), ("GRID", (0,0), (-1,-1), .2, colors.lightgrey), ("ALIGN", (2,1), (3,-1), "RIGHT")]))
        story.append(tbl)
    story.append(Spacer(1, .4 * cm))
    story.append(Paragraph("Disclaimer", styles["Heading3"]))
    story.append(Paragraph("Este material é meramente informativo e apresenta uma simulação de carteira teórica com base em parâmetros internos de alocação. Não constitui promessa de rentabilidade, garantia de resultado, oferta pública ou recomendação individualizada sem análise de suitability.", styles["MWSmall"]))
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
    page = st.radio("Navegação", ["💰 Controle de Saldo", "🎯 Asset Allocation", "📄 Carteira Teórica", "🛠️ Diagnóstico"], index=0)

st.title("M Wealth - Balanceamento de Carteiras")
st.caption("Aplicativo focado em controle de saldo, asset allocation e carteira teórica para uso operacional.")


# =============================================================================
# Página 1 - Controle de saldo
# =============================================================================
if page == "💰 Controle de Saldo":
    st.header("Controle de Saldo para Operação")
    st.markdown('<div class="mw-muted">Tela enxuta para identificar quem tem caixa disponível, caixa negativo ou recursos fora da estratégia que podem demandar ação.</div>', unsafe_allow_html=True)
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

    st.caption(f"Carregamento: **{mode}** | Linhas: **{len(df_latest):,}** | Última consolidação: **{meta.get('built_at', 'n/d')}**")

    saldo_mask = df_latest["subbucket"].eq("Saldo em Conta") | df_latest["subbucket"].eq("Caixa Internacional")
    saldo_conta = df_latest[saldo_mask].groupby(["GRUPO GERAL", "CLIENTE", "corretora", "conta"], dropna=False, as_index=False)["valor_mercado"].sum().rename(columns={"valor_mercado": "Saldo"})
    pl_conta = df_latest.groupby(["GRUPO GERAL", "CLIENTE", "corretora", "conta"], dropna=False, as_index=False)["valor_mercado"].sum().rename(columns={"valor_mercado": "PL"})
    fora_conta = df_latest[df_latest["classe_macro"].eq("Fora da Estratégia")].groupby(["GRUPO GERAL", "CLIENTE", "corretora", "conta"], dropna=False, as_index=False)["valor_mercado"].sum().rename(columns={"valor_mercado": "Fora da Estratégia"})
    painel = pl_conta.merge(saldo_conta, how="left", on=["GRUPO GERAL", "CLIENTE", "corretora", "conta"]).merge(fora_conta, how="left", on=["GRUPO GERAL", "CLIENTE", "corretora", "conta"]).fillna({"Saldo": 0.0, "Fora da Estratégia": 0.0})
    painel["Saldo % PL"] = np.where(painel["PL"] != 0, painel["Saldo"] / painel["PL"], 0)
    painel["Prioridade"] = np.select(
        [painel["Saldo"] >= 50000, painel["Saldo"] >= 10000, painel["Saldo"] >= min_saldo, painel["Saldo"] < 0],
        ["Alta", "Média", "Baixa", "Caixa negativo"],
        default="Sem ação",
    )
    painel["Ação sugerida"] = np.where(painel["Saldo"] < 0, "Verificar chamada/margem", np.where(painel["Saldo"] >= min_saldo, "Avaliar operação", "Sem ação"))

    operaveis = painel[(painel["Saldo"] >= min_saldo) | (painel["Saldo"] < 0)].sort_values(["Prioridade", "Saldo"], ascending=[True, False])
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Contas com saldo ≥ mínimo", int((painel["Saldo"] >= min_saldo).sum()))
    k2.metric("Saldo total disponível", format_brl(painel.loc[painel["Saldo"] > 0, "Saldo"].sum()))
    k3.metric("Caixa negativo", format_brl(painel.loc[painel["Saldo"] < 0, "Saldo"].sum()))
    k4.metric("Fora da estratégia", format_brl(painel["Fora da Estratégia"].sum()))

    st.subheader("Contas que merecem ação")
    cols = ["GRUPO GERAL", "CLIENTE", "corretora", "conta", "PL", "Saldo", "Saldo % PL", "Fora da Estratégia", "Prioridade", "Ação sugerida"]
    st.dataframe(prepare_display(operaveis[cols], money_cols=["PL", "Saldo", "Fora da Estratégia"], pct_cols=["Saldo % PL"], max_rows=700), use_container_width=True, hide_index=True)

    with st.expander("Ver todas as contas, inclusive sem saldo operacional", expanded=False):
        st.dataframe(prepare_display(painel[cols].sort_values("Saldo", ascending=False), money_cols=["PL", "Saldo", "Fora da Estratégia"], pct_cols=["Saldo % PL"], max_rows=1200), use_container_width=True, hide_index=True)


# =============================================================================
# Página 2 - Asset Allocation
# =============================================================================
if page == "🎯 Asset Allocation":
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
if page == "📄 Carteira Teórica":
    st.header("Carteira Teórica - Simulador Comercial")
    modelos = list(pesos.keys())
    if not modelos:
        st.error("Pesos-alocacao.xlsx não foi encontrado ou não pôde ser lido.")
        st.stop()
    c1, c2, c3 = st.columns([2, 1.2, 2])
    with c1:
        modelo = st.selectbox("Modelo", modelos)
    with c2:
        valor = st.number_input("Patrimônio simulado", min_value=0.0, value=1_000_000.0, step=100_000.0, format="%.2f")
    with c3:
        cliente = st.text_input("Nome do cliente no PDF (opcional)")
    df_teor = theoretical_portfolio(pesos[modelo], valor, modelo)
    macro = df_teor[df_teor["Nível"].eq("Subbucket")].groupby("Classe", as_index=False)["Valor"].sum()
    macro["Peso"] = np.where(valor > 0, macro["Valor"] / valor, 0)
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Valor", format_brl(valor))
    k2.metric("RF Brasil", format_brl(macro.loc[macro["Classe"].eq("RF Brasil"), "Valor"].sum()))
    k3.metric("RV Brasil", format_brl(macro.loc[macro["Classe"].eq("RV Brasil"), "Valor"].sum()))
    k4.metric("Internacional", format_brl(macro.loc[macro["Classe"].eq("Internacional"), "Valor"].sum()))
    c1, c2 = st.columns(2)
    with c1:
        fig = px.pie(macro, names="Classe", values="Valor", title="Alocação Teórica", hole=.45)
        fig.update_layout(height=300, margin=dict(l=8, r=8, t=45, b=8))
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        sub = df_teor[df_teor["Nível"].eq("Subbucket")].sort_values("Valor", ascending=True)
        fig = px.bar(sub, x="Valor", y="Subbucket", orientation="h", title="Abertura por Subbucket")
        fig.update_layout(height=300, margin=dict(l=8, r=8, t=45, b=8))
        st.plotly_chart(fig, use_container_width=True)
    st.dataframe(prepare_display(df_teor, money_cols=["Valor"], pct_cols=["Peso"]), use_container_width=True, hide_index=True)
    try:
        pdf = build_pdf_teorico(df_teor, modelo, valor, cliente)
        st.download_button("Baixar PDF", data=pdf, file_name=f"carteira_teorica_mwealth_{modelo.lower().replace(' ', '_')}.pdf", mime="application/pdf", type="primary")
    except Exception as e:
        st.info(f"PDF indisponível: {e}")


# =============================================================================
# Diagnóstico leve
# =============================================================================
if page == "🛠️ Diagnóstico":
    st.header("Diagnóstico Técnico")
    sig = posmod.source_signature()
    diag_rows = []
    for nome, info in sig.items():
        diag_rows.append({"Arquivo": nome, "Caminho": info.get("path"), "Existe": not info.get("missing", False), "Tamanho": info.get("size", 0), "Modificado": info.get("modified", "")})
    st.dataframe(pd.DataFrame(diag_rows), use_container_width=True, hide_index=True)
    st.write({"latest_is_stale": posmod.latest_is_stale(), "latest_pickle": str(posmod.LATEST_PICKLE), "exists": posmod.LATEST_PICKLE.exists(), "meta": str(posmod.LATEST_META)})
    if posmod.LATEST_META.exists():
        try:
            st.json(json.loads(posmod.LATEST_META.read_text(encoding="utf-8")))
        except Exception as e:
            st.warning(f"Não consegui ler meta: {e}")
    if st.button("Limpar cache do Streamlit"):
        st.cache_data.clear()
        st.success("Cache limpo. Recarregue a página.")

st.caption("M Wealth Asset Allocation")
