from __future__ import annotations

import json
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

st.set_page_config(page_title="M Wealth | Asset Allocation", layout="wide", page_icon="📊")

BASE_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
POS_DIR = BASE_DIR / "posicoes"
APP_VERSION = "2.4 estabilidade"

# Estratégia de RV e FiInfra fica no código, conforme orientação da gestão.
ACOES_SEM_RENDA = ["AXIA3", "EQTL3", "SBSP3", "ITUB3", "BPAC11", "PSSA3", "PRIO3", "VALE3", "WEGE3", "RENT3"]
ACOES_COM_RENDA = ["CPLE3", "EGIE3", "AXIA3", "ITUB3", "VALE3", "ALOS3", "FLRY3", "ABEV3", "PRIO3", "WEGE3"]
FIIS_RECOMENDADOS = ["KNRI11", "XPML11", "HGLG11", "PVBI11", "HGRU11", "KNCR11", "KNIP11", "KNCA11"]
FI_INFRA_TICKERS = ["KNDI11", "CDII11", "IFRI11", "AZQI11", "KNCE11", "AZIN11", "JURO11", "IFRA11", "KDIF11", "JGPI11", "BDIF11", "JMBI11", "CPTI11"]

SUBBUCKET_ORDER = [
    "Pós - Imediato", "Pós - 1 a 30 dias", "Pós - 31 a 180 dias", "Pós - 181 a 360 dias", "Pós - 361+ dias",
    "FiInfra e Cetipados", "Pré - Bancário", "Pré - Tesouro", "Inflação - Bancário", "Inflação - Tesouro",
    "Crédito Privado", "Ações", "FIIs", "Renda Fixa Internacional", "Renda Variável Internacional",
    "Saldo em Conta", "Fundos de Investimento / Sem Liquidez Mapeada", "COE / Estruturados", "Previdência", "Outros / Não Classificado",
]

st.markdown("""
<style>
.block-container { padding-top: 1.0rem; padding-bottom: 2.0rem; }
div[data-testid="stMetricValue"] { font-size: 1.25rem; }
.mw-small { color: rgba(250,250,250,.66); font-size: .86rem; }
.mw-line { border-top: 1px solid rgba(255,255,255,.09); margin: .8rem 0 1rem 0; }
</style>
""", unsafe_allow_html=True)


def find_file(filename: str) -> Path:
    for p in [POS_DIR / filename, BASE_DIR / filename, Path(filename)]:
        if p.exists():
            return p
    return POS_DIR / filename


def logo_path() -> Path | None:
    for p in [BASE_DIR / "Logo-M-Wealth.png", POS_DIR / "Logo-M-Wealth.png"]:
        if p.exists():
            return p
    return None


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(s)) if not unicodedata.combining(c))


def norm(x) -> str:
    return strip_accents(str(x or "")).upper().strip()


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


def acao_por_diff(diff: float, tolerancia: float = 100.0) -> str:
    if diff > tolerancia:
        return "Comprar / Aportar"
    if diff < -tolerancia:
        return "Vender / Reduzir"
    return "Manter / OK"


def prepare_display(df: pd.DataFrame, money_cols: list[str] | None = None, pct_cols: list[str] | None = None, qty_cols: list[str] | None = None, max_rows: int | None = None) -> pd.DataFrame:
    """Evita pandas Styler, que estava pesando demais e derrubando a tela."""
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
            out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0).map(lambda x: f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
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
        pesos[carteira][a] = w
    return {k: v for k, v in pesos.items() if v}


@st.cache_data(show_spinner=False)
def load_contas_cached() -> pd.DataFrame:
    try:
        return posmod.load_control_accounts()
    except Exception:
        return pd.DataFrame()


@st.cache_data(show_spinner="Carregando base consolidada...")
def load_positions_cached(force_rebuild: bool = False) -> tuple[pd.DataFrame, dict, str]:
    """Carregamento controlado. Só reconstrói quando não há base, quando o usuário força ou quando os arquivos fonte mudaram."""
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


def classify_position(row: pd.Series) -> pd.Series:
    corretora = norm(row.get("corretora", ""))
    asset_id = norm(row.get("asset_id", "")).replace(" ", "")
    text = " ".join(norm(row.get(c, "")) for c in ["asset_id", "asset_nome", "asset_tipo", "mercado", "sub_mercado", "estrategia"])

    if corretora == "CS":
        if any(x in text for x in ["BOND", "FIXED", "TREASURY", "CD ", "CERTIFICATE", "NOTE"]):
            return pd.Series(["Internacional", "Renda Fixa Internacional", "Renda Fixa Internacional"])
        return pd.Series(["Internacional", "Renda Variável Internacional", "Renda Variável Internacional"])
    if any(x in text for x in ["SALDO", "FINANCEIRO", "CONTA CORRENTE", "CUSTODIA REMUNERADA"]):
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
    if any(x in text for x in ["TESOURO SELIC", "LFT", "SELIC", "IMEDIATO", "D+0", "D+1"]):
        return pd.Series(["RF Brasil", "Pós - Imediato", "Pós - Imediato"])
    if any(x in text for x in ["TESOURO PRE", "NTN-F", "NTNF", "LTN"]):
        return pd.Series(["RF Brasil", "Pré - Tesouro", "Pré - Tesouro"])
    if any(x in text for x in ["TESOURO IPCA", "NTN-B", "NTNB"]):
        return pd.Series(["RF Brasil", "Inflação - Tesouro", "Inflação - Tesouro"])
    if any(x in text for x in ["IPCA", "INFLACAO", "INFLAÇÃO"]):
        return pd.Series(["RF Brasil", "Inflação - Bancário", "Inflação - Bancário"])
    if any(x in text for x in ["PRE-FIXADO", "PRE FIXADO", "PRÉ-FIXADO", "PREFIXADO"]):
        return pd.Series(["RF Brasil", "Pré - Bancário", "Pré - Bancário"])
    if any(x in text for x in ["CRI", "CRA", "DEBENTURE", "CDCA", "CREDITO PRIVADO", "CRÉDITO PRIVADO"]):
        return pd.Series(["RF Brasil", "Crédito Privado", "Crédito Privado"])
    if any(x in text for x in ["CDB", "LCI", "LCA", "LCD", "COMPROMISSADA", "POS-FIXADO", "PÓS-FIXADO", "CDI"]):
        return pd.Series(["RF Brasil", "Pós - 361+ dias", "Pós - 361+ dias"])
    if any(x in text for x in ["FIC", "FIM", "FIRF", "FIDC", "FUNDO", "FUNDOS"]):
        return pd.Series(["RF Brasil", "Fundos de Investimento / Sem Liquidez Mapeada", "Fundos de Investimento / Sem Liquidez Mapeada"])
    return pd.Series(["Outros", "Outros / Não Classificado", "Outros / Não Classificado"])


@st.cache_data(show_spinner=False)
def enrich_positions_cached(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df = df.loc[:, ~df.columns.duplicated()].copy()
    df = df.drop(columns=[c for c in ["classe_macro", "subclasse", "subbucket"] if c in df.columns], errors="ignore")
    for c in ["valor_mercado", "quantidade"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    cols = df.apply(classify_position, axis=1)
    cols.columns = ["classe_macro", "subclasse", "subbucket"]
    return pd.concat([df, cols], axis=1).loc[:, ~pd.concat([df, cols], axis=1).columns.duplicated()].copy()


def peso_get(p: dict[str, float], key: str) -> float:
    key_n = norm(key)
    for k, v in p.items():
        if norm(k) == key_n:
            return float(v or 0)
    total = 0.0
    for k, v in p.items():
        if key_n in norm(k):
            total += float(v or 0)
    return total


def macro_targets_from_model(p: dict[str, float], pl: float) -> pd.DataFrame:
    rv = peso_get(p, "RV Brasil")
    intl = peso_get(p, "Internacional")
    rf = max(0.0, 1.0 - rv - intl)
    return pd.DataFrame({
        "Classe": ["RF Brasil", "RV Brasil", "Internacional"],
        "Peso Ideal": [rf, rv, intl],
        "Valor Ideal": [pl * rf, pl * rv, pl * intl],
    })


def subbucket_targets_from_model(p: dict[str, float], pl: float) -> pd.DataFrame:
    rows = []
    ignored = {"RV BRASIL", "INTERNACIONAL"}
    for k, v in p.items():
        if norm(k) in ignored:
            continue
        w = float(v or 0)
        if w <= 0:
            continue
        kn = norm(k)
        if any(x in kn for x in ["ACAO", "AÇÕES", "ACOES"]):
            classe, bucket = "RV Brasil", "Ações"
        elif "FII" in kn:
            classe, bucket = "RV Brasil", "FIIs"
        elif "INTERNACIONAL" in kn:
            classe, bucket = "Internacional", "Renda Variável Internacional"
        elif "FIINFRA" in kn or "CETIP" in kn:
            classe, bucket = "RF Brasil", "FiInfra e Cetipados"
        elif "INFL" in kn or "IPCA" in kn:
            classe, bucket = "RF Brasil", "Inflação - Bancário"
        elif "PRE" in kn:
            classe, bucket = "RF Brasil", "Pré - Bancário"
        elif "POS" in kn or "PÓS" in kn or "CDI" in kn or "IMEDIATO" in kn:
            classe, bucket = "RF Brasil", k
        elif "FUNDO" in kn:
            classe, bucket = "RF Brasil", "Fundos de Investimento / Sem Liquidez Mapeada"
        else:
            classe, bucket = "RF Brasil", k
        rows.append([classe, bucket, w, pl * w])
    df = pd.DataFrame(rows, columns=["Classe", "Subbucket", "Peso Ideal", "Valor Ideal"])
    if df.empty:
        return df
    return df.groupby(["Classe", "Subbucket"], as_index=False).sum()


def model_for_profile(perfil: str, modelos: list[str]) -> str:
    pn = norm(perfil)
    preferred = []
    if "ARROJADO RENDA CONSTRUCAO" in pn: preferred.append("Arrojado Renda Construção")
    if "MODERADO RENDA CONSTRUCAO" in pn: preferred.append("Moderado Renda Construção")
    if "CONSERVADOR RENDA CONSTRUCAO" in pn: preferred.append("Conservador Renda Construção")
    if "ARROJADO RENDA USUFRUTO" in pn: preferred.append("Arrojado Renda Usufruto")
    if "MODERADO RENDA USUFRUTO" in pn: preferred.append("Moderado Renda Usufruto")
    if "CONSERVADOR RENDA USUFRUTO" in pn: preferred.append("Conservador Renda Usufruto")
    if "ULTRACONSERVADOR" in pn: preferred.append("Ultraconservador")
    if "CONSERVADOR" in pn: preferred.append("Conservador")
    if "MODERADO" in pn: preferred.append("Moderado")
    if "ARROJADO" in pn: preferred.append("Arrojado")
    for pref in preferred:
        for m in modelos:
            if norm(m) == norm(pref):
                return m
    return modelos[0] if modelos else ""


def rv_universe(modelo: str) -> dict[str, list[str]]:
    return {"Ações": ACOES_COM_RENDA if "RENDA" in norm(modelo) else ACOES_SEM_RENDA, "FIIs": FIIS_RECOMENDADOS}


def portfolio_tables(pos_cliente: pd.DataFrame, p: dict[str, float], pl: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    macro_target = macro_targets_from_model(p, pl)
    actual_macro = pos_cliente.groupby("classe_macro", as_index=False)["valor_mercado"].sum()
    actual_macro["Classe"] = actual_macro["classe_macro"].replace({"Caixa": "Saldo em Conta", "Outros": "Outros / Fora da Estratégia"})
    actual_macro = actual_macro.groupby("Classe", as_index=False)["valor_mercado"].sum().rename(columns={"valor_mercado": "Valor Atual"})
    macro = actual_macro.merge(macro_target, how="outer", on="Classe").fillna(0)
    macro["Peso Atual"] = np.where(pl > 0, macro["Valor Atual"] / pl, 0)
    macro["Diferença"] = macro["Valor Ideal"] - macro["Valor Atual"]
    macro["Ação"] = macro["Diferença"].apply(acao_por_diff)
    macro = macro[["Classe", "Peso Atual", "Peso Ideal", "Valor Atual", "Valor Ideal", "Diferença", "Ação"]]

    sub_target = subbucket_targets_from_model(p, pl)
    actual_sub = pos_cliente.groupby(["classe_macro", "subbucket"], as_index=False)["valor_mercado"].sum()
    actual_sub = actual_sub.rename(columns={"classe_macro": "Classe", "subbucket": "Subbucket", "valor_mercado": "Valor Atual"})
    sub = actual_sub.merge(sub_target, how="outer", on=["Classe", "Subbucket"]).fillna(0)
    sub["Peso Atual"] = np.where(pl > 0, sub["Valor Atual"] / pl, 0)
    sub["Diferença"] = sub["Valor Ideal"] - sub["Valor Atual"]
    sub["Ação"] = sub["Diferença"].apply(acao_por_diff)
    order = {b: i for i, b in enumerate(SUBBUCKET_ORDER)}
    sub["_ord"] = sub["Subbucket"].map(order).fillna(999)
    sub = sub.sort_values(["Classe", "_ord", "Subbucket"]).drop(columns="_ord")
    sub = sub[["Classe", "Subbucket", "Peso Atual", "Peso Ideal", "Valor Atual", "Valor Ideal", "Diferença", "Ação"]]
    return macro, sub


def rv_recommendation(pos_cliente: pd.DataFrame, p: dict[str, float], pl: float, modelo: str) -> pd.DataFrame:
    rows = []
    for bucket, tickers in rv_universe(modelo).items():
        alvo_total = pl * peso_get(p, bucket)
        alvo_ativo = alvo_total / len(tickers) if tickers else 0
        for t in tickers:
            mask = pos_cliente["asset_id"].astype(str).str.upper().str.strip().eq(t)
            atual = float(pos_cliente.loc[mask, "valor_mercado"].sum())
            qtd = float(pos_cliente.loc[mask, "quantidade"].sum())
            diff = alvo_ativo - atual
            rows.append([bucket, t, qtd, atual, alvo_ativo, diff, acao_por_diff(diff)])
    return pd.DataFrame(rows, columns=["Subbucket", "Ativo", "Qtd Atual", "Valor Atual", "Valor Ideal", "Diferença", "Ação"])


def theoretical_portfolio(p: dict[str, float], valor: float, modelo: str) -> pd.DataFrame:
    rows = []
    sub = subbucket_targets_from_model(p, valor)
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


# Dados leves no início
pesos = load_pesos_xlsx(str(find_file("Pesos-alocacao.xlsx")))
df_contas = load_contas_cached()

with st.sidebar:
    lp = logo_path()
    if lp:
        st.image(str(lp), use_container_width=True)
    st.caption(f"Versão {APP_VERSION}")
    page = st.radio("Navegação", ["📌 Posições", "🎯 Asset Allocation", "📄 Carteira Teórica", "🛠️ Diagnóstico"], index=0)

st.title("M Wealth - Asset Allocation")
st.caption("Versão estabilizada: menos recarregamento pesado, sem pandas Styler em tabelas grandes e com rebuild controlado.")

# -------------------- Posições --------------------
if page == "📌 Posições":
    st.header("Painel de Posições Consolidadas")
    c0, c1 = st.columns([1.2, 4])
    force = c0.button("Atualizar base agora", type="primary")
    if force:
        st.cache_data.clear()
    try:
        df_latest, meta, mode = load_positions_cached(force_rebuild=force)
    except Exception as e:
        st.error(f"Falha ao carregar/consolidar posições: {e}")
        st.stop()
    df_latest = enrich_positions_cached(df_latest)
    st.caption(f"Modo de carregamento: **{mode}** | Linhas: **{len(df_latest):,}** | Atualizado em: **{meta.get('built_at', 'n/d')}**")

    pl_total = float(df_latest["valor_mercado"].sum())
    grupos = int(df_latest["GRUPO GERAL"].dropna().nunique()) if "GRUPO GERAL" in df_latest.columns else 0
    contas = int(df_latest[["corretora", "conta"]].drop_duplicates().shape[0])
    saldo = float(df_latest.loc[df_latest["subbucket"].eq("Saldo em Conta"), "valor_mercado"].sum())
    nao_class = int(df_latest["subbucket"].eq("Outros / Não Classificado").sum())
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("PL Total", format_brl(pl_total))
    k2.metric("Grupos", grupos)
    k3.metric("Contas", contas)
    k4.metric("Saldo em conta", format_brl(saldo))
    k5.metric("Linhas sem classificação", nao_class)

    resumo_corretora = df_latest.groupby("corretora", as_index=False).agg(PL=("valor_mercado", "sum"), Contas=("conta", "nunique"), Ativos=("asset_id", "nunique")).sort_values("PL", ascending=False)
    col1, col2 = st.columns(2)
    with col1:
        fig = px.pie(resumo_corretora, names="corretora", values="PL", title="PL por Corretora", hole=.45)
        fig.update_layout(height=300, margin=dict(l=8, r=8, t=45, b=8))
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        dist = df_latest.groupby("subbucket", as_index=False)["valor_mercado"].sum().sort_values("valor_mercado", ascending=True).tail(12)
        fig = px.bar(dist, x="valor_mercado", y="subbucket", orientation="h", title="Distribuição por Subbucket")
        fig.update_layout(height=300, margin=dict(l=8, r=8, t=45, b=8))
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Resumo por Corretora")
    st.dataframe(prepare_display(resumo_corretora, money_cols=["PL"]), use_container_width=True, hide_index=True)

    col3, col4 = st.columns(2)
    with col3:
        st.subheader("Top 10 Grupos por PL")
        if "GRUPO GERAL" in df_latest.columns:
            top = df_latest.groupby("GRUPO GERAL", as_index=False)["valor_mercado"].sum().sort_values("valor_mercado", ascending=False).head(10)
            top.columns = ["Grupo", "PL"]
            st.dataframe(prepare_display(top, money_cols=["PL"]), use_container_width=True, hide_index=True)
    with col4:
        st.subheader("Controle de Qualidade")
        sem_grupo = int(df_latest["GRUPO GERAL"].isna().sum()) if "GRUPO GERAL" in df_latest.columns else len(df_latest)
        sem_cliente = int(df_latest["CLIENTE"].isna().sum()) if "CLIENTE" in df_latest.columns else len(df_latest)
        valor_nao_class = float(df_latest.loc[df_latest["subbucket"].eq("Outros / Não Classificado"), "valor_mercado"].sum())
        qc = pd.DataFrame({"Item": ["Linhas sem grupo", "Linhas sem cliente", "Valor não classificado", "PL em saldo"], "Resultado": [sem_grupo, sem_cliente, format_brl(valor_nao_class), format_brl(saldo)]})
        st.dataframe(qc, use_container_width=True, hide_index=True)

    with st.expander("Ver ativos consolidados — limitado aos 500 maiores para não travar o navegador", expanded=False):
        cols = ["corretora", "conta", "GRUPO GERAL", "CLIENTE", "asset_id", "asset_nome", "asset_tipo", "classe_macro", "subbucket", "valor_mercado", "quantidade", "moeda"]
        view = df_latest[[c for c in cols if c in df_latest.columns]].sort_values("valor_mercado", ascending=False)
        st.dataframe(prepare_display(view, money_cols=["valor_mercado"], qty_cols=["quantidade"], max_rows=500), use_container_width=True, hide_index=True)

    with st.expander("Ativos / linhas sem classificação", expanded=False):
        unc = df_latest[df_latest["subbucket"].eq("Outros / Não Classificado")].sort_values("valor_mercado", ascending=False)
        if unc.empty:
            st.success("Nenhuma linha sem classificação encontrada.")
        else:
            cols = ["corretora", "conta", "GRUPO GERAL", "CLIENTE", "asset_id", "asset_nome", "asset_tipo", "mercado", "sub_mercado", "valor_mercado"]
            st.dataframe(prepare_display(unc[[c for c in cols if c in unc.columns]], money_cols=["valor_mercado"], max_rows=300), use_container_width=True, hide_index=True)

# -------------------- Asset Allocation --------------------
if page == "🎯 Asset Allocation":
    st.header("Asset Allocation - Cliente / Grupo Familiar")
    try:
        df_latest, meta, mode = load_positions_cached(force_rebuild=False)
    except Exception as e:
        st.error(f"Não consegui carregar a base: {e}")
        st.stop()
    df_latest = enrich_positions_cached(df_latest)
    if "GRUPO GERAL" not in df_latest.columns:
        st.error("A base não possui a coluna GRUPO GERAL. Verifique o arquivo Contas.xlsx.")
        st.stop()
    grupos = sorted(df_latest["GRUPO GERAL"].dropna().astype(str).unique())
    if not grupos:
        st.warning("Nenhum grupo familiar encontrado na base.")
        st.stop()

    col_g, col_c, col_m = st.columns([3, 3, 2])
    with col_g:
        grupo_sel = st.selectbox("Grupo Geral", grupos)
    contas_info = df_latest[df_latest["GRUPO GERAL"].astype(str).eq(str(grupo_sel))][["conta", "CLIENTE", "corretora"]].drop_duplicates()
    with col_c:
        opcoes = ["Todas as contas"] + [f"{str(r.CLIENTE).strip()} • {r.corretora} ({r.conta})" if pd.notna(r.CLIENTE) else f"{r.corretora} ({r.conta})" for r in contas_info.itertuples(index=False)]
        conta_sel = st.selectbox("Conta", opcoes)
    with col_m:
        perfil_cliente = "Não identificado"
        if not df_contas.empty and "GRUPO GERAL" in df_contas.columns:
            m = df_contas[df_contas["GRUPO GERAL"].astype(str).str.strip().eq(str(grupo_sel).strip())]
            if not m.empty:
                col_perfil = next((c for c in m.columns if norm(c) == "PERFIL CARTEIRA"), None)
                if col_perfil:
                    perfil_cliente = str(m[col_perfil].iloc[0]).strip()
        modelos = list(pesos.keys())
        idx = modelos.index(model_for_profile(perfil_cliente, modelos)) if modelos else 0
        modelo = st.selectbox("Modelo", modelos, index=idx)

    pos_cliente = df_latest[df_latest["GRUPO GERAL"].astype(str).eq(str(grupo_sel))].copy()
    if conta_sel != "Todas as contas":
        conta_real = conta_sel.split("(")[-1].strip(")")
        pos_cliente = pos_cliente[pos_cliente["conta"].astype(str).eq(conta_real)].copy()
    p = pesos[modelo]
    pl = float(pos_cliente["valor_mercado"].sum())

    pl_xp = float(pos_cliente.loc[pos_cliente["corretora"].eq("XP"), "valor_mercado"].sum())
    pl_btg = float(pos_cliente.loc[pos_cliente["corretora"].eq("BTG"), "valor_mercado"].sum())
    pl_cs = float(pos_cliente.loc[pos_cliente["corretora"].eq("CS"), "valor_mercado"].sum())
    saldo = float(pos_cliente.loc[pos_cliente["subbucket"].eq("Saldo em Conta"), "valor_mercado"].sum())
    fora = float(pos_cliente.loc[pos_cliente["classe_macro"].isin(["Outros", "Caixa"]), "valor_mercado"].sum())
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("PL Total", format_brl(pl), delta=f"Perfil: {perfil_cliente}")
    k2.metric("XP", format_brl(pl_xp))
    k3.metric("BTG", format_brl(pl_btg))
    k4.metric("CS", format_brl(pl_cs))
    k5.metric("Saldo/Fora", format_brl(saldo + fora))

    macro_df, sub_df = portfolio_tables(pos_cliente, p, pl)
    st.subheader("1. Visão Macro Geral")
    c1, c2 = st.columns(2)
    with c1:
        plot = macro_df[macro_df["Valor Atual"] > 0]
        fig = px.pie(plot, names="Classe", values="Valor Atual", title="Carteira Atual", hole=.45)
        fig.update_layout(height=300, margin=dict(l=8, r=8, t=45, b=8))
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        plot = macro_df[macro_df["Valor Ideal"] > 0]
        fig = px.pie(plot, names="Classe", values="Valor Ideal", title="Carteira Ideal", hole=.45)
        fig.update_layout(height=300, margin=dict(l=8, r=8, t=45, b=8))
        st.plotly_chart(fig, use_container_width=True)
    st.dataframe(prepare_display(macro_df, money_cols=["Valor Atual", "Valor Ideal", "Diferença"], pct_cols=["Peso Atual", "Peso Ideal"]), use_container_width=True, hide_index=True)

    st.subheader("2. Subbuckets da Alocação")
    st.dataframe(prepare_display(sub_df, money_cols=["Valor Atual", "Valor Ideal", "Diferença"], pct_cols=["Peso Atual", "Peso Ideal"]), use_container_width=True, hide_index=True)

    st.subheader("3. Detalhamento por Estratégia")
    buckets = [b for b in SUBBUCKET_ORDER if b in set(pos_cliente["subbucket"])]
    bucket_sel = st.selectbox("Escolha o subbucket para detalhar", buckets if buckets else ["Nenhum"])
    if bucket_sel != "Nenhum":
        subpos = pos_cliente[pos_cliente["subbucket"].eq(bucket_sel)].copy()
        agrup = subpos.groupby(["asset_id", "asset_nome", "corretora"], dropna=False, as_index=False).agg(Valor=("valor_mercado", "sum"), Quantidade=("quantidade", "sum"), Contas=("conta", "nunique")).sort_values("Valor", ascending=False)
        agrup["Peso no Cliente"] = np.where(pl > 0, agrup["Valor"] / pl, 0)
        st.dataframe(prepare_display(agrup, money_cols=["Valor"], pct_cols=["Peso no Cliente"], qty_cols=["Quantidade"], max_rows=500), use_container_width=True, hide_index=True)

    st.subheader("4. Sugestão Estratégica de RV Brasil")
    rv_df = rv_recommendation(pos_cliente, p, pl, modelo)
    st.dataframe(prepare_display(rv_df, money_cols=["Valor Atual", "Valor Ideal", "Diferença"], qty_cols=["Qtd Atual"]), use_container_width=True, hide_index=True)

    with st.expander("Ativos fora da estratégia / revisão", expanded=False):
        universo = set(ACOES_SEM_RENDA + ACOES_COM_RENDA + FIIS_RECOMENDADOS + FI_INFRA_TICKERS)
        fora_df = pos_cliente[
            pos_cliente["classe_macro"].isin(["Outros", "Caixa"]) |
            ((pos_cliente["classe_macro"].eq("RV Brasil")) & (~pos_cliente["asset_id"].astype(str).str.upper().isin(universo))) |
            (pos_cliente["subbucket"].str.contains("Sem Liquidez|Não Classificado|COE|Previdência", case=False, na=False))
        ].sort_values("valor_mercado", ascending=False)
        cols = ["corretora", "conta", "asset_id", "asset_nome", "classe_macro", "subbucket", "valor_mercado", "quantidade"]
        st.dataframe(prepare_display(fora_df[[c for c in cols if c in fora_df.columns]], money_cols=["valor_mercado"], qty_cols=["quantidade"], max_rows=300), use_container_width=True, hide_index=True)

# -------------------- Teórica --------------------
if page == "📄 Carteira Teórica":
    st.header("Carteira Teórica - Simulador")
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

# -------------------- Diagnóstico --------------------
if page == "🛠️ Diagnóstico":
    st.header("Diagnóstico Técnico")
    st.write("Use essa tela quando o app cair ou ficar branco. Ela carrega pouco conteúdo e ajuda a identificar se o problema é arquivo, cache ou base consolidada.")
    st.subheader("Arquivos fonte")
    sig = posmod.source_signature()
    diag_rows = []
    for nome, info in sig.items():
        diag_rows.append({"Arquivo": nome, "Caminho": info.get("path"), "Existe": not info.get("missing", False), "Tamanho": info.get("size", 0), "Modificado": info.get("modified", "")})
    st.dataframe(pd.DataFrame(diag_rows), use_container_width=True, hide_index=True)
    st.subheader("Cache/base")
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
