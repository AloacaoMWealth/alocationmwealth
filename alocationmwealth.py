from __future__ import annotations

import json
import math
import re
import unicodedata
from datetime import datetime
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

try:
    import yfinance as yf
    HAS_YFINANCE = True
except Exception:
    yf = None
    HAS_YFINANCE = False

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
APP_VERSION = "4.8"

# Estratégia de RV e FiInfra permanece no código, conforme orientação da gestão.
ACOES_SEM_RENDA = ["AXIA3", "EQTL3", "SBSP3", "ITUB3", "BPAC11", "PSSA3", "PRIO3", "VALE3", "WEGE3", "RENT3"]
ACOES_COM_RENDA = ["CPLE3", "EGIE3", "AXIA3", "ITUB3", "VALE3", "ALOS3", "FLRY3", "ABEV3", "PRIO3", "WEGE3"]
FIIS_RECOMENDADOS = ["KNRI11", "XPML11", "HGLG11", "PVBI11", "HGRU11", "KNCR11", "KNIP11", "KNCA11"]
FI_INFRA_TICKERS = ["KNDI11", "CDII11", "IFRI11", "AZIN11", "JURO11", "IFRA11", "KDIF11", "BDIF11", "JMBI11", "CPTI11"]
FI_INFRA_POS_TICKERS = ["KNDI11", "CDII11", "IFRI11", "AZIN11"]
FI_INFRA_INFLACAO_TICKERS = ["JURO11", "IFRA11", "KDIF11", "BDIF11", "JMBI11", "CPTI11"]

# Ativos estratégicos negociados no Brasil que precisam ser tratados pelo código
# antes da regra genérica de ticker terminado em 11, para não caírem como FII.
ATIVOS_ESTRATEGICOS_B3 = {
    # Regras explícitas têm prioridade sobre a heurística genérica de ticker final 11.
    "BPAC11": {"classe": "RV Brasil", "subbucket": "Ações", "estrategia": "Ação / Unit"},
    "NTNS11": {"classe": "RF Brasil", "subbucket": "Inflação - Tesouro", "estrategia": "ETF de renda fixa — Tesouro IPCA 0 a 4 anos"},
    "BITH11": {"classe": "Alternativos", "subbucket": "Bitcoin", "estrategia": "Bitcoin"},
    "GOLD11": {"classe": "Alternativos", "subbucket": "Ouro", "estrategia": "Ouro"},
    "UTLL11": {"classe": "RV Brasil", "subbucket": "Ações", "estrategia": "Utilities"},
    "DIVD11": {"classe": "RV Brasil", "subbucket": "Ações", "estrategia": "Dividendos"},
    "XINA11": {"classe": "Internacional", "subbucket": "Renda Variável Internacional", "estrategia": "Exposição China"},
    "IVVB11": {"classe": "Internacional", "subbucket": "Renda Variável Internacional", "estrategia": "Exposição EUA"},
    "NASD11": {"classe": "Internacional", "subbucket": "Renda Variável Internacional", "estrategia": "Exposição EUA"},
    "SPYI11": {"classe": "Internacional", "subbucket": "Renda Variável Internacional", "estrategia": "Dividendos EUA"},
    "ALUG11": {"classe": "Internacional", "subbucket": "Renda Variável Internacional", "estrategia": "Imobiliário EUA"},
    "USTK11": {"classe": "Internacional", "subbucket": "Renda Variável Internacional", "estrategia": "Tecnologia EUA"},
}

# Mantém ordem operacional das tabelas.
SUBBUCKET_ORDER = [
    "Pós - Imediato", "Pós - 1 a 30 dias", "Pós - 31 a 180 dias", "Pós - 181 a 360 dias", "Pós - 361+ dias",
    "FiInfra e Cetipados", "Pré - Bancário", "Pré - Tesouro", "Inflação - Bancário", "Inflação - Tesouro", "Crédito Privado",
    "Ações", "FIIs", "Bitcoin", "Ouro", "Renda Fixa Internacional", "Renda Variável Internacional", "Caixa Internacional",
    "Saldo em Conta", "Proventos a Receber", "Direitos de Subscrição", "Fundos de Investimento / Sem Liquidez Mapeada", "Previdência", "COE / Estruturados", "Outros / Não Classificado",
]

st.markdown(
    """
    <style>
    [data-testid="stSidebar"], [data-testid="collapsedControl"] { display: none !important; }
    .block-container { padding-top: 1.0rem; padding-bottom: 2.0rem; max-width: 1680px; }
    div[data-testid="stMetricValue"] { font-size: 1.2rem; }
    .mw-header { display: flex; align-items: center; gap: 1.2rem; margin-bottom: .35rem; }
    .mw-title { font-size: 2.2rem; line-height: 1.1; font-weight: 800; margin: 0; }
    .mw-version { color: rgba(250,250,250,.45); font-size: .78rem; margin-top: .25rem; }
    .mw-card { border: 1px solid rgba(255,255,255,.10); border-radius: 16px; padding: 16px 18px; background: linear-gradient(180deg, rgba(255,255,255,.055), rgba(255,255,255,.022)); box-shadow: 0 10px 24px rgba(0,0,0,.12); }
    .mw-card-label { color: rgba(250,250,250,.68); font-size: .82rem; font-weight: 650; margin-bottom: .35rem; }
    .mw-card-value { color: #fff; font-size: 1.38rem; font-weight: 800; letter-spacing: -.02em; }
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


def has_token(text: str, *terms: str) -> bool:
    """Procura termos como tokens, evitando COE dentro de PARTICIPACOES, FIA em CONFIANCA etc."""
    txt = norm(text)
    for term in terms:
        pattern = r"(?<![A-Z0-9])" + re.escape(norm(term)).replace(r"\ ", r"\s+") + r"(?![A-Z0-9])"
        if re.search(pattern, txt):
            return True
    return False


def has_any_phrase(text: str, terms: list[str] | tuple[str, ...]) -> bool:
    return any(has_token(text, term) for term in terms)


# Personalizações operacionais de liquidez. Chaves podem ser CNPJ (somente dígitos)
# ou trechos inequívocos do nome normalizado. 999 representa sem liquidez operacional />720 dias.
LIQUIDITY_OVERRIDES: dict[str, float] = {
    "CETIPADO": 999.0,
}


def liquidity_override_for_row(row: pd.Series) -> float:
    cnpj = only_digits_str(row.get("cnpj", ""))
    text = norm(" ".join([str(row.get("asset_id", "")), str(row.get("asset_nome", "")), str(row.get("manual_fundo", "")), str(row.get("manual_classe", ""))]))
    for key, days in LIQUIDITY_OVERRIDES.items():
        key_digits = only_digits_str(key)
        if key_digits and len(key_digits) >= 8 and cnpj == key_digits:
            return float(days)
        if not key_digits and has_token(text, key):
            return float(days)
    return np.nan


def format_brl_label(v) -> str:
    """Versão segura para labels markdown do Streamlit, evitando que o $ vire formatação."""
    return format_brl(v).replace("$", "\\$")


def format_brl(v) -> str:
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def parse_brl_input(value, default: float = 0.0) -> float:
    """Converte campos digitados no padrão brasileiro, ex.: R$ 10.000,00."""
    try:
        s = str(value or "").replace("R$", "").replace(" ", "").strip()
        if not s:
            return default
        s = s.replace(".", "").replace(",", ".")
        return float(s)
    except Exception:
        return default


def metric_card(label: str, value: str) -> None:
    html = (
        '<div class="mw-card">'
        f'<div class="mw-card-label">{escape(str(label))}</div>'
        f'<div class="mw-card-value">{escape(str(value))}</div>'
        '</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def ensure_saldo_operacional(df: pd.DataFrame) -> pd.DataFrame:
    """Garante identificação de saldo real mesmo quando o cache veio de versão antiga."""
    out = df.copy()
    if "saldo_operacional" not in out.columns:
        out["saldo_operacional"] = False
    base = out["saldo_operacional"].fillna(False).astype(bool)
    idx = out.index
    corretora = out.get("corretora", pd.Series([""] * len(out), index=idx)).astype(str).str.upper().str.strip()
    asset_id = out.get("asset_id", pd.Series([""] * len(out), index=idx)).astype(str).str.upper().str.strip()
    asset_nome = out.get("asset_nome", pd.Series([""] * len(out), index=idx)).astype(str).str.upper().str.strip()
    asset_tipo = out.get("asset_tipo", pd.Series([""] * len(out), index=idx)).astype(str).str.upper().str.strip()
    mercado = out.get("mercado", pd.Series([""] * len(out), index=idx)).astype(str).str.upper().str.strip()
    texto = asset_id + " " + asset_nome + " " + asset_tipo + " " + mercado
    xp_saldo = corretora.eq("XP") & asset_tipo.eq("FINANCEIRO") & asset_id.str.contains("SALDO", na=False)
    btg_saldo = corretora.eq("BTG") & (mercado.eq("CONTA CORRENTE") | asset_nome.eq("CONTA CORRENTE") | asset_id.eq("CONTA CORRENTE"))
    cs_saldo = corretora.eq("CS") & texto.str.contains("CASH|MONEY MARKET|SWEEP|BANK DEPOSIT", regex=True, na=False)
    out["saldo_operacional"] = (base | xp_saldo | btg_saldo | cs_saldo).fillna(False).astype(bool)
    return out


def friendly_class_name(x: str) -> str:
    mapa = {
        "RF Brasil": "Renda fixa no Brasil",
        "RV Brasil": "Renda variável no Brasil",
        "Internacional": "Investimentos internacionais",
        "Caixa": "Caixa",
        "Fora da Estratégia": "Fora da estratégia",
        "Alternativos": "Alternativos",
    }
    return mapa.get(str(x), str(x))


def friendly_strategy_name(x: str) -> str:
    mapa = {
        "Pós - Imediato": "Pós-fixado — liquidez imediata",
        "Pós - 1 a 30 dias": "Pós-fixado — resgate entre 1 e 30 dias",
        "Pós - 31 a 180 dias": "Pós-fixado — resgate entre 31 e 180 dias",
        "Pós - 181 a 360 dias": "Pós-fixado — resgate entre 181 e 360 dias",
        "Pós - 361+ dias": "Pós-fixado — resgate acima de 361 dias",
        "FiInfra e Cetipados": "Fundos de infraestrutura e crédito incentivado",
        "Pré - Bancário": "Prefixado bancário",
        "Pré - Tesouro": "Tesouro prefixado",
        "Inflação - Bancário": "Inflação bancário",
        "Inflação - Tesouro": "Tesouro indexado à inflação",
        "Crédito Privado": "Crédito privado",
        "Ações": "Ações brasileiras",
        "FIIs": "Fundos imobiliários",
        "Bitcoin": "Bitcoin",
        "Ouro": "Ouro",
        "Renda Fixa Internacional": "Renda fixa internacional",
        "Renda Variável Internacional": "Renda variável internacional",
        "Caixa Internacional": "Caixa internacional",
        "Saldo em Conta": "Saldo em conta",
        "Proventos a Receber": "Proventos a receber",
        "Direitos de Subscrição": "Direitos de subscrição",
        "Fundos de Investimento / Sem Liquidez Mapeada": "Fundos de investimento sem liquidez mapeada",
        "Previdência": "Previdência",
        "COE / Estruturados": "COE e estruturados",
        "Outros / Não Classificado": "Outros não classificados",
    }
    return mapa.get(str(x), str(x))


def money_color_styler(df: pd.DataFrame, money_cols=None, pct_cols=None, qty_cols=None, diff_cols=None):
    """Styler leve para tabelas pequenas, com diferença positiva em verde e negativa em vermelho."""
    money_cols = [c for c in (money_cols or []) if c in df.columns]
    pct_cols = [c for c in (pct_cols or []) if c in df.columns]
    qty_cols = [c for c in (qty_cols or []) if c in df.columns]
    diff_cols = [c for c in (diff_cols or []) if c in df.columns]

    fmt = {}
    for c in money_cols:
        fmt[c] = format_brl
    for c in pct_cols:
        fmt[c] = fmt_pct
    for c in qty_cols:
        fmt[c] = lambda x: "" if pd.isna(x) else f"{float(x):,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def color_diff(v):
        try:
            val = float(v)
        except Exception:
            return ""
        if val > 0:
            return "color: #7bd88f; font-weight: 800;"
        if val < 0:
            return "color: #ff6b6b; font-weight: 800;"
        return "color: rgba(250,250,250,.72);"

    styler = df.style.format(fmt)
    if diff_cols:
        styler = styler.map(color_diff, subset=diff_cols)
    return styler


def macro_hierarchy_table(sub_df: pd.DataFrame, pl: float) -> pd.DataFrame:
    """Monta uma visão macro no estilo Power BI: classe principal + aberturas relevantes.

    A ideia é mostrar a carteira em uma leitura executiva, sem ficar presa só em
    Renda Fixa / Renda Variável / Internacional, mas também sem detalhar ativo a ativo.
    """
    if sub_df.empty:
        return pd.DataFrame(columns=[
            "Estratégia", "Quanto tem", "Quanto tem %", "Deveria ter (%)",
            "Deveria ter R$", "Ajuste necessário", "Diferença"
        ])

    base = sub_df.copy()
    for col in ["Valor Atual", "Valor Ideal", "Peso Atual", "Peso Ideal", "Diferença"]:
        if col in base.columns:
            base[col] = pd.to_numeric(base[col], errors="coerce").fillna(0.0)

    def group_row(label: str, buckets: list[str] | None = None, classes: list[str] | None = None, is_header: bool = False):
        if classes is not None:
            part = base[base["Classe"].isin(classes)].copy()
        else:
            part = base[base["Subbucket"].isin(buckets or [])].copy()
        atual = float(part["Valor Atual"].sum()) if not part.empty else 0.0
        ideal = float(part["Valor Ideal"].sum()) if not part.empty else 0.0
        diff = ideal - atual
        return {
            "Estratégia": label,
            "Quanto tem": atual,
            "Quanto tem %": atual / pl if pl > 0 else 0.0,
            "Deveria ter (%)": ideal / pl if pl > 0 else 0.0,
            "Deveria ter R$": ideal,
            "Ajuste necessário": diff,
            "Diferença": (atual - ideal) / pl if pl > 0 else 0.0,
            "_header": is_header,
        }

    groups = [
        ("RENDA FIXA NO BRASIL", None, ["RF Brasil"], True),
        ("  Pós-fixado", ["Pós - Imediato", "Pós - 1 a 30 dias", "Pós - 31 a 180 dias", "Pós - 181 a 360 dias", "Pós - 361+ dias"], None, False),
        ("  Prefixado", ["Pré - Bancário", "Pré - Tesouro"], None, False),
        ("  Inflação", ["Inflação - Bancário", "Inflação - Tesouro"], None, False),
        ("  Infraestrutura e crédito incentivado", ["FiInfra e Cetipados"], None, False),
        ("  Crédito privado", ["Crédito Privado"], None, False),
        ("RENDA VARIÁVEL NO BRASIL", None, ["RV Brasil"], True),
        ("  Ações brasileiras", ["Ações"], None, False),
        ("  Fundos imobiliários", ["FIIs"], None, False),
        ("INVESTIMENTOS INTERNACIONAIS", None, ["Internacional"], True),
        ("  Renda fixa internacional", ["Renda Fixa Internacional"], None, False),
        ("  Renda variável internacional", ["Renda Variável Internacional"], None, False),
        ("ALTERNATIVOS", None, ["Alternativos"], True),
        ("  Bitcoin", ["Bitcoin"], None, False),
        ("  Ouro", ["Ouro"], None, False),
        ("FORA DA ESTRATÉGIA / MONITORAMENTO", None, ["Fora da Estratégia"], True),
        ("  Fundos sem liquidez mapeada", ["Fundos de Investimento / Sem Liquidez Mapeada"], None, False),
        ("  Previdência", ["Previdência"], None, False),
        ("  COE e estruturados", ["COE / Estruturados"], None, False),
        ("  Outros não classificados", ["Outros / Não Classificado"], None, False),
    ]

    rows = []
    for label, buckets, classes, is_header in groups:
        row = group_row(label, buckets=buckets, classes=classes, is_header=is_header)
        # Mostra sempre os cabeçalhos principais quando houver qualquer valor atual/ideal.
        # Nas linhas-filhas, remove ruído de zeros.
        if is_header:
            if abs(row["Quanto tem"]) > 0.01 or abs(row["Deveria ter R$"]) > 0.01:
                rows.append(row)
        else:
            if abs(row["Quanto tem"]) > 0.01 or abs(row["Deveria ter R$"]) > 0.01:
                rows.append(row)

    total_atual = float(base["Valor Atual"].sum())
    total_ideal = float(base["Valor Ideal"].sum())
    rows.append({
        "Estratégia": "TOTAL",
        "Quanto tem": total_atual,
        "Quanto tem %": total_atual / pl if pl > 0 else 0.0,
        "Deveria ter (%)": total_ideal / pl if pl > 0 else 0.0,
        "Deveria ter R$": total_ideal,
        "Ajuste necessário": total_ideal - total_atual,
        "Diferença": (total_atual - total_ideal) / pl if pl > 0 else 0.0,
        "_header": True,
    })
    return pd.DataFrame(rows)


def macro_hierarchy_styler(df: pd.DataFrame):
    """Aplica visual leve na tabela macro hierárquica."""
    visible_cols = ["Estratégia", "Quanto tem", "Quanto tem %", "Deveria ter (%)", "Deveria ter R$", "Ajuste necessário", "Diferença"]
    view = df[visible_cols].copy()
    header_mask = df.get("_header", pd.Series([False] * len(df))).astype(bool).tolist()

    fmt = {
        "Quanto tem": format_brl,
        "Quanto tem %": fmt_pct,
        "Deveria ter (%)": fmt_pct,
        "Deveria ter R$": format_brl,
        "Ajuste necessário": format_brl,
        "Diferença": fmt_pct,
    }

    def color_adjust(v):
        try:
            val = float(v)
        except Exception:
            return ""
        if val > 0:
            return "color: #7bd88f; font-weight: 800;"
        if val < 0:
            return "color: #ff6b6b; font-weight: 800;"
        return "color: rgba(250,250,250,.72);"

    def row_style(row):
        is_header = header_mask[row.name] if row.name < len(header_mask) else False
        if is_header:
            return [
                "background-color: rgba(93, 115, 170, .34); font-weight: 900; border-top: 1px solid rgba(255,255,255,.22);"
                for _ in row
            ]
        return ["" for _ in row]

    styler = view.style.format(fmt).apply(row_style, axis=1)
    styler = styler.map(color_adjust, subset=["Ajuste necessário"])
    return styler


def bucket_from_liquidity_days(days) -> str:
    try:
        d = float(days)
    except Exception:
        return "Fundos de Investimento / Sem Liquidez Mapeada"
    if d <= 1:
        return "Pós - Imediato"
    if d <= 30:
        return "Pós - 1 a 30 dias"
    if d <= 180:
        return "Pós - 31 a 180 dias"
    if d <= 360:
        return "Pós - 181 a 360 dias"
    return "Pós - 361+ dias"


def fund_name_key(value: str) -> str:
    """Chave robusta para casar fundos entre posição e Manual de Alocação.

    Remove pedaços jurídicos/comerciais que mudam entre corretoras, mas preserva
    os termos realmente distintivos do fundo/gestora.
    """
    s = norm(value)
    replacements = {
        "CRÉDITO PRIVADO": "CREDITO PRIVADO",
        "AÇÕES": "ACOES",
        "PREVIDÊNCIA": "PREVIDENCIA",
    }
    for a, b in replacements.items():
        s = s.replace(a, b)
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    stop = [
        "FUNDO", "FUNDOS", "INVESTIMENTO", "INVESTIMENTOS", "COTAS", "COTA",
        "FIC", "FIF", "FIRF", "FIM", "FIA", "FIDC", "FI", "RF", "CP", "RL", "LP",
        "DE", "EM", "DO", "DA", "DAS", "DOS", "ADVISORY", "CREDITO", "PRIVADO",
        "PREVIDENCIA", "PGBL", "VGBL", "HIGH"  # HIGH fica pouco distintivo sozinho
    ]
    tokens = [t for t in re.split(r"\s+", s.strip()) if t and t not in stop]
    return " ".join(tokens)


def fund_token_set(value: str) -> set[str]:
    key = fund_name_key(value)
    return {t for t in key.split() if len(t) >= 3}


def fund_match_score(position_name: str, manual_name: str) -> float:
    """Score simples, sem dependência externa, para casar nomes parecidos.
    Prioriza sobreposição de tokens relevantes e sequência parcial.
    """
    a = fund_name_key(position_name)
    b = fund_name_key(manual_name)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.95
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    # mínimo de 2 tokens ajuda Jive BossaNova, BNP Rubi etc. sem casar coisas genéricas demais.
    token_score = len(inter) / max(1, min(len(ta), len(tb)))
    seq_score = 0.0
    try:
        from difflib import SequenceMatcher
        seq_score = SequenceMatcher(None, a, b).ratio()
    except Exception:
        seq_score = 0.0
    return max(token_score, seq_score * 0.82)


@st.cache_data(show_spinner=False)
def load_manual_fundos_cached(path_str: str, mtime_ns: int = 0, file_size: int = 0) -> pd.DataFrame:
    """Lê o Manual de Alocação antigo, quando estiver disponível."""
    path = Path(path_str)
    if not path.exists():
        return pd.DataFrame(columns=["Fundo", "Classificação", "Liquidez (D+)", "Previdência", "CNPJ", "manual_key", "cnpj_norm"])
    try:
        raw = pd.read_excel(path, sheet_name="Gestoras e Fundos", header=None)
        header_row = None
        for i, row in raw.iterrows():
            vals = [str(x).strip() for x in row.tolist()]
            if "Gestora" in vals and "Fundo" in vals:
                header_row = i
                break
        if header_row is None:
            return pd.DataFrame(columns=["Fundo", "Classificação", "Liquidez (D+)", "Previdência", "CNPJ", "manual_key", "cnpj_norm"])
        df = pd.read_excel(path, sheet_name="Gestoras e Fundos", header=header_row)
        df.columns = [str(c).strip() for c in df.columns]
        keep = [c for c in ["Gestora", "Classificação", "Fundo", "Liquidez (D+)", "Perfil", "Condição", "Previdência", "Estretégia/Objetivo", "CNPJ"] if c in df.columns]
        df = df[keep].copy()
        df = df[df.get("Fundo", pd.Series(dtype=object)).notna()].copy()
        df["Fundo"] = df["Fundo"].astype(str).str.strip()
        if "CNPJ" not in df.columns:
            df["CNPJ"] = ""
        df["manual_key"] = df["Fundo"].apply(fund_name_key)
        df["cnpj_norm"] = df["CNPJ"].apply(only_digits_str)
        df["Liquidez (D+)"] = pd.to_numeric(df.get("Liquidez (D+)", np.nan), errors="coerce")
        df = df[df["manual_key"].str.len() > 2].drop_duplicates("manual_key")
        return df.reset_index(drop=True)
    except Exception:
        return pd.DataFrame(columns=["Fundo", "Classificação", "Liquidez (D+)", "Previdência", "CNPJ", "manual_key", "cnpj_norm"])


def only_digits_str(value) -> str:
    raw = str(value or "").strip()
    # Excel frequentemente entrega CNPJ como float textual (ex.: 123...0).
    if re.fullmatch(r"\d+\.0", raw):
        raw = raw[:-2]
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits[:14] if len(digits) > 14 and raw.endswith(".0") else digits


def parse_days_from_text(value) -> float:
    """Extrai qualquer prazo de liquidez válido.

    A versão anterior aceitava apenas uma lista fechada de números. Assim, prazos
    reais como 44, 85, 119, 365 e 999 eram ignorados; quando a liquidação era D+1,
    o fundo acabava incorretamente no bucket de liquidez imediata.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return np.nan
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value) if float(value) >= 0 else np.nan
    txt = norm(value)
    if not txt or txt in ["NAN", "NONE", "NULL", "-"]:
        return np.nan
    m = re.search(r"D\s*\+\s*(\d+(?:[.,]\d+)?)", txt)
    if not m:
        # Aceita qualquer inteiro/decimal isolado, não apenas uma lista pré-definida.
        m = re.search(r"(?<![A-Z0-9])(\d+(?:[.,]\d+)?)(?![A-Z0-9])", txt)
    if not m:
        return np.nan
    try:
        return float(m.group(1).replace(",", "."))
    except Exception:
        return np.nan


def file_cache_signature(path: Path) -> tuple[int, int]:
    """Assinatura usada para invalidar cache quando uma planilha é substituída."""
    try:
        stt = path.stat()
        return int(stt.st_mtime_ns), int(stt.st_size)
    except Exception:
        return 0, 0


@st.cache_data(show_spinner=False)
def load_fundos_prev_cached(path_str: str, mtime_ns: int = 0, file_size: int = 0) -> pd.DataFrame:
    """Lê a planilha Nome fundos e prev.xlsx, priorizando CNPJ, classificação e liquidez.

    Esta base é mais operacional para casar fundos da XP e BTG do que o manual antigo.
    """
    path = Path(path_str)
    cols = ["Fundo", "Classificação", "Liquidez (D+)", "Previdência", "CNPJ", "manual_key", "cnpj_norm", "Fonte", "Liquidez Operacional", "Classe Operacional", "Subbucket Operacional"]
    if not path.exists():
        return pd.DataFrame(columns=cols)
    rows = []
    try:
        xls = pd.ExcelFile(path)
        if "Fundos de Investimentos" in xls.sheet_names:
            df = pd.read_excel(path, sheet_name="Fundos de Investimentos")
            df.columns = [str(c).strip() for c in df.columns]
            # Colunas opcionais permitem exceções no próprio arquivo mestre, sem editar código.
            # Exemplos: LIQUIDEZ_OPERACIONAL=999 e SUBBUCKET_OPERACIONAL="FiInfra e Cetipados".
            for _, r in df.iterrows():
                nome = str(r.get("NOME_FUNDO", "")).strip()
                if not nome or nome.lower() == "nan":
                    continue
                cot = parse_days_from_text(r.get("COTIZAÇÃO_RESGATE", np.nan))
                liq = parse_days_from_text(r.get("LIQUIDAÇÃO_RESGATE", np.nan))
                # Para alocação, o prazo de disponibilidade precisa considerar cotização + liquidação.
                prazo = np.nan
                if pd.notna(cot) or pd.notna(liq):
                    prazo = (0 if pd.isna(cot) else cot) + (0 if pd.isna(liq) else liq)
                rows.append({
                    "Fundo": nome,
                    "Classificação": str(r.get("CLASSIFICAÇÃO_XP", r.get("CLASSIFICAÇÃO_CVM", ""))).strip(),
                    "Classificação CVM": str(r.get("CLASSIFICAÇÃO_CVM", "")).strip(),
                    "Liquidez (D+)": prazo,
                    "Previdência": "Não",
                    "CNPJ": str(r.get("CNPJ_FUNDO", "")).strip(),
                    "manual_key": fund_name_key(nome),
                    "cnpj_norm": only_digits_str(r.get("CNPJ_FUNDO", "")),
                    "Fonte": "Nome fundos e prev",
                    "Liquidez Operacional": parse_days_from_text(r.get("LIQUIDEZ_OPERACIONAL", np.nan)),
                    "Classe Operacional": str(r.get("CLASSE_OPERACIONAL", "")).strip(),
                    "Subbucket Operacional": str(r.get("SUBBUCKET_OPERACIONAL", "")).strip(),
                })
        if "Previdência" in xls.sheet_names:
            df = pd.read_excel(path, sheet_name="Previdência")
            df.columns = [str(c).strip() for c in df.columns]
            for _, r in df.iterrows():
                nome = str(r.get("Nome do Fundo Investido pelos planos", "")).strip()
                if not nome or nome.lower() == "nan":
                    continue
                prazo = parse_days_from_text(r.get("Liquidação ", r.get("Liquidação", np.nan)))
                if pd.isna(prazo):
                    prazo = parse_days_from_text(r.get("Carência inicial para Port. Internas\n(dias corridos)", np.nan))
                rows.append({
                    "Fundo": nome,
                    "Classificação": str(r.get("Classificação XP", "")).strip(),
                    "Classificação CVM": "Previdência",
                    "Liquidez (D+)": prazo,
                    "Previdência": "Sim",
                    "CNPJ": str(r.get("CNPJ", "")).strip(),
                    "manual_key": fund_name_key(nome),
                    "cnpj_norm": only_digits_str(r.get("CNPJ", "")),
                    "Fonte": "Nome fundos e prev - Prev",
                })
    except Exception:
        pass
    out = pd.DataFrame(rows, columns=cols + ["Classificação CVM"])
    if not out.empty and "Liquidez Operacional" in out.columns:
        op = pd.to_numeric(out["Liquidez Operacional"], errors="coerce")
        out.loc[op.notna(), "Liquidez (D+)"] = op[op.notna()]
    if out.empty:
        return pd.DataFrame(columns=cols)
    out = out[out["manual_key"].astype(str).str.len() > 2].copy()
    # Prioriza CNPJ; mantém duplicatas de nome quando CNPJ diferente.
    out = out.drop_duplicates(subset=["cnpj_norm", "manual_key"], keep="first")
    return out.reset_index(drop=True)

def apply_manual_fund_mapping(df: pd.DataFrame) -> pd.DataFrame:
    """Casa fundos por hierarquia: CNPJ > nome exato > candidatos por tokens > fuzzy restrito.

    Evita o custo quadrático de comparar cada posição contra toda a base e mantém
    a base operacional Nome fundos e prev como fonte prioritária sobre o manual antigo.
    """
    out = df.copy()
    fundos_path = find_file("Nome fundos e prev.xlsx")
    manual_path = find_file("Manual de Alocação.xlsx")
    fundos_sig = file_cache_signature(fundos_path)
    manual_sig = file_cache_signature(manual_path)
    base_nova = load_fundos_prev_cached(str(fundos_path), *fundos_sig)
    base_manual = load_manual_fundos_cached(str(manual_path), *manual_sig)
    bases = []
    if not base_nova.empty:
        bn = base_nova.copy(); bn["_priority"] = 0; bases.append(bn)
    if not base_manual.empty:
        bm = base_manual.copy(); bm["Fonte"] = "Manual de Alocação"; bm["_priority"] = 1
        if "Classificação CVM" not in bm.columns: bm["Classificação CVM"] = ""
        bases.append(bm)
    manual = pd.concat(bases, ignore_index=True, sort=False) if bases else pd.DataFrame()

    for col in ["manual_match", "manual_classe", "manual_liquidez", "manual_previdencia", "manual_fundo", "manual_fonte", "manual_score", "manual_metodo", "manual_classe_operacional", "manual_subbucket_operacional"]:
        if col not in out.columns:
            out[col] = False if col == "manual_match" else (np.nan if col in ["manual_liquidez", "manual_score"] else "")
    if manual.empty:
        out["manual_match"] = False
        return out

    for c in ["asset_nome", "asset_id", "cnpj"]:
        if c not in out.columns: out[c] = ""
    out["_cnpj_norm"] = out["cnpj"].apply(only_digits_str)
    out["_fund_key_asset"] = out["asset_nome"].astype(str).apply(fund_name_key)
    empty_key = out["_fund_key_asset"].str.len().le(2)
    out.loc[empty_key, "_fund_key_asset"] = out.loc[empty_key, "asset_id"].astype(str).apply(fund_name_key)

    manual["cnpj_norm"] = manual.get("cnpj_norm", "").apply(only_digits_str)
    manual["manual_key"] = manual.get("manual_key", manual.get("Fundo", "")).astype(str)
    manual = manual.sort_values("_priority", kind="stable").drop_duplicates(["cnpj_norm", "manual_key"], keep="first")
    records = manual.to_dict("records")
    by_cnpj = {}
    by_key = {}
    token_index: dict[str, set[int]] = {}
    for i, rec in enumerate(records):
        cnpj = rec.get("cnpj_norm", "")
        key = str(rec.get("manual_key", ""))
        if len(cnpj) >= 8 and cnpj not in by_cnpj: by_cnpj[cnpj] = i
        if key and key not in by_key: by_key[key] = i
        for tok in set(key.split()):
            if len(tok) >= 4: token_index.setdefault(tok, set()).add(i)

    def find_match(cnpj: str, key: str, original_name: str = ""):
        cnpj = only_digits_str(cnpj)
        if cnpj and cnpj in by_cnpj: return records[by_cnpj[cnpj]], 1.0, "CNPJ"
        if key in by_key: return records[by_key[key]], 1.0, "Nome exato"
        tokens = {t for t in key.split() if len(t) >= 4}
        candidate_ids: set[int] = set()
        for tok in tokens: candidate_ids.update(token_index.get(tok, set()))
        if not candidate_ids: return None, 0.0, ""
        best_score, best_rec = 0.0, None
        source_name = original_name or key
        for i in candidate_ids:
            rec = records[i]
            score = fund_match_score(source_name, str(rec.get("Fundo", "")))
            if score > best_score: best_score, best_rec = score, rec
        return (best_rec, best_score, "Nome aproximado") if best_score >= 0.78 else (None, best_score, "")

    matches = [find_match(c, k, n) for c, k, n in zip(out["_cnpj_norm"], out["_fund_key_asset"], out["asset_nome"].astype(str))]
    recs = pd.Series([m[0] for m in matches], index=out.index)
    scores = pd.Series([m[1] for m in matches], index=out.index)
    methods = pd.Series([m[2] for m in matches], index=out.index)
    out["manual_match"] = recs.notna()
    mask = out["manual_match"]
    out.loc[mask, "manual_classe"] = recs[mask].apply(lambda r: str(r.get("Classificação", "")).strip())
    out.loc[mask, "manual_liquidez"] = recs[mask].apply(lambda r: pd.to_numeric(r.get("Liquidez (D+)", np.nan), errors="coerce"))
    out.loc[mask, "manual_previdencia"] = recs[mask].apply(lambda r: str(r.get("Previdência", "")).strip())
    out.loc[mask, "manual_fundo"] = recs[mask].apply(lambda r: str(r.get("Fundo", "")).strip())
    out.loc[mask, "manual_fonte"] = recs[mask].apply(lambda r: str(r.get("Fonte", "")).strip())
    out.loc[mask, "manual_classe_operacional"] = recs[mask].apply(lambda r: str(r.get("Classe Operacional", "")).strip())
    out.loc[mask, "manual_subbucket_operacional"] = recs[mask].apply(lambda r: str(r.get("Subbucket Operacional", "")).strip())
    out.loc[mask, "manual_score"] = scores[mask]
    out.loc[mask, "manual_metodo"] = methods[mask]
    return out.drop(columns=["_fund_key_asset", "_cnpj_norm"], errors="ignore")

def exchange_position_mask(df: pd.DataFrame) -> pd.Series:
    """Identifica somente posições econômicas reais negociadas em bolsa.

    Exclui proventos, eventos, custódia remunerada, opções e direitos de
    subscrição. Essas linhas não representam a quantidade efetivamente usada
    no rebalanceamento e podem duplicar ou distorcer a posição.
    """
    if df.empty:
        return pd.Series(dtype=bool, index=df.index)
    idx = df.index
    asset_tipo = df.get("asset_tipo", pd.Series("", index=idx)).astype(str).map(norm)
    mercado = df.get("mercado", pd.Series("", index=idx)).astype(str).map(norm)
    ticker = df.get("ticker_norm", pd.Series("", index=idx)).astype(str)
    classe = df.get("subbucket", pd.Series("", index=idx)).astype(str)

    real = (
        asset_tipo.isin(["ACOES", "FUNDOS IMOBILIARIOS"])
        | mercado.isin(["RENDA VARIAVEL", "RENDA VARIÁVEL"])
        | classe.isin(["Ações", "FIIs", "FiInfra e Cetipados"])
    )
    excluded = asset_tipo.str.contains(
        "PROVENT|CUSTODIA REMUNERADA|PROVISAO|EVENTO|OPCOES|OPÇÕES|OPCAO|OPÇÃO",
        regex=True,
        na=False,
    )
    subscription_right = ticker.str.match(r"^[A-Z]{4}12$", na=False)
    valid_ticker = ticker.str.match(r"^[A-Z]{4}[0-9]{1,2}$", na=False)
    return (real & ~excluded & ~subscription_right & valid_ticker).fillna(False)


def yahoo_symbol(ticker: str) -> str:
    """Converte ticker B3 para o padrão do Yahoo Finance."""
    tk = ticker_clean(ticker)
    return f"{tk}.SA" if tk else ""


def _last_close_from_download(data: pd.DataFrame, yahoo_ticker: str) -> float:
    """Extrai o último Close válido de respostas simples ou MultiIndex."""
    if data is None or data.empty:
        return np.nan
    try:
        if isinstance(data.columns, pd.MultiIndex):
            # yfinance pode retornar (campo, ticker) ou (ticker, campo).
            if ("Close", yahoo_ticker) in data.columns:
                series = data[("Close", yahoo_ticker)]
            elif (yahoo_ticker, "Close") in data.columns:
                series = data[(yahoo_ticker, "Close")]
            else:
                candidates = [c for c in data.columns if "Close" in tuple(map(str, c)) and yahoo_ticker in tuple(map(str, c))]
                if not candidates:
                    return np.nan
                series = data[candidates[0]]
        else:
            if "Close" not in data.columns:
                return np.nan
            series = data["Close"]
        series = pd.to_numeric(series, errors="coerce").dropna()
        return float(series.iloc[-1]) if not series.empty else np.nan
    except Exception:
        return np.nan


@st.cache_data(ttl=300, show_spinner="Atualizando cotações de mercado...")
def load_yfinance_prices(tickers: tuple[str, ...]) -> dict[str, float]:
    """Busca preços recentes da B3 no Yahoo Finance.

    Primeiro tenta o último negócio intradiário de 1 minuto. Quando o Yahoo não
    disponibiliza intraday para algum ativo, utiliza o último fechamento diário.
    O cache de cinco minutos evita chamadas repetidas em cada interação do app.
    """
    clean = sorted({ticker_clean(t) for t in tickers if ticker_clean(t)})
    if not clean or not HAS_YFINANCE:
        return {}

    symbols = {tk: yahoo_symbol(tk) for tk in clean}
    yahoo_list = list(symbols.values())
    prices: dict[str, float] = {}

    try:
        intraday = yf.download(
            tickers=yahoo_list,
            period="1d",
            interval="1m",
            auto_adjust=False,
            progress=False,
            threads=True,
            group_by="column",
        )
        for tk, sym in symbols.items():
            px = _last_close_from_download(intraday, sym)
            if pd.notna(px) and px > 0:
                prices[tk] = float(px)
    except Exception:
        pass

    missing = [symbols[tk] for tk in clean if tk not in prices]
    if missing:
        try:
            daily = yf.download(
                tickers=missing,
                period="5d",
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=True,
                group_by="column",
            )
            reverse = {v: k for k, v in symbols.items()}
            for sym in missing:
                px = _last_close_from_download(daily, sym)
                if pd.notna(px) and px > 0:
                    prices[reverse[sym]] = float(px)
        except Exception:
            pass
    return prices


def mark_exchange_positions_to_market(df: pd.DataFrame, prices: dict[str, float]) -> pd.DataFrame:
    """Remarca posições reais de bolsa por quantidade × preço atual."""
    out = df.copy()
    mask = exchange_position_mask(out)
    if mask.empty or not mask.any():
        return out
    out.loc[mask, "quantidade"] = pd.to_numeric(out.loc[mask, "quantidade"], errors="coerce").fillna(0.0)
    live = out.loc[mask, "ticker_norm"].map(prices)
    valid = live.notna() & live.gt(0)
    idx_valid = live.index[valid]
    out.loc[idx_valid, "preco_mercado_atual"] = live.loc[idx_valid].astype(float)
    out.loc[idx_valid, "valor_mercado"] = (
        out.loc[idx_valid, "quantidade"].astype(float)
        * out.loc[idx_valid, "preco_mercado_atual"].astype(float)
    )
    return out


def current_exchange_position(pos_cliente: pd.DataFrame, ticker: str, price_ref: dict[str, float]) -> tuple[float, float, float]:
    """Retorna preço, quantidade real e valor atual remarcado de um ticker."""
    tk = ticker_clean(ticker)
    mask = pos_cliente.get("ticker_norm", pd.Series("", index=pos_cliente.index)).eq(tk) & exchange_position_mask(pos_cliente)
    qtd = float(pd.to_numeric(pos_cliente.loc[mask, "quantidade"], errors="coerce").fillna(0.0).sum())
    preco = float(price_ref.get(tk, np.nan))
    atual = qtd * preco if pd.notna(preco) and preco > 0 else np.nan
    return preco, qtd, atual


def operation_text(qtd_diff, diff_value) -> str:
    try:
        q = float(qtd_diff)
        v = float(diff_value)
    except Exception:
        return "Preço indisponível"
    if np.isnan(q):
        return "Preço indisponível"
    q_abs = abs(int(round(q)))
    if q_abs == 0:
        return "Manter"
    if v > 0:
        return f"Comprar {q_abs}"
    if v < 0:
        return f"Vender {q_abs}"
    return "Manter"



def basket_excel_bytes(df_orders: pd.DataFrame, cliente: str = "") -> BytesIO:
    """Gera um Excel simples de basket para acelerar a execução."""
    base = df_orders.copy()
    if base.empty or "Qtd a operar" not in base.columns:
        out = pd.DataFrame(columns=["C/V", "Ativo", "Quantidade", "Preço referência", "Valor estimado", "Cliente/Grupo"])
    else:
        base["Qtd a operar"] = pd.to_numeric(base["Qtd a operar"], errors="coerce").fillna(0)
        base = base[base["Qtd a operar"].round().ne(0)].copy()
        out = pd.DataFrame({
            "C/V": np.where(base["Qtd a operar"] > 0, "C", "V"),
            "Ativo": base["Ativo"].astype(str),
            "Quantidade": base["Qtd a operar"].abs().round().astype(int),
            "Preço referência": pd.to_numeric(base.get("Preço referência", np.nan), errors="coerce"),
            "Valor estimado": pd.to_numeric(base.get("Diferença", 0), errors="coerce").abs(),
            "Cliente/Grupo": cliente,
        })
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        out.to_excel(writer, index=False, sheet_name="Basket")
    buf.seek(0)
    return buf


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
        return "Abaixo do alvo"
    if diff < -tol:
        return "Acima do alvo"
    return "Dentro do alvo"


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
    """Abre imediatamente o último consolidado disponível.

    O rebuild completo ficou restrito ao botão de atualização ou à ausência do
    cache. Isso evita reler todos os arquivos XP/BTG/CS a cada inicialização —
    comportamento especialmente lento em deploys onde o mtime dos arquivos muda.
    """
    if force_rebuild:
        df = posmod.build_latest_from_repo()
        mode = "rebuild_manual"
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

def classify_fund_class(class_text: str, full_text: str, liquidez_days=np.nan) -> tuple[str, str]:
    """Converte a classificação de fundos para a estrutura operacional.

    Fundos de renda fixa, crédito e FIDC são distribuídos exclusivamente pelo
    prazo de liquidez. A natureza do fundo não deve contaminar o bucket de
    títulos de crédito privado, reservado a ativos diretos como debêntures,
    CRI, CRA e similares.
    """
    c = norm(class_text)
    t = norm(full_text)
    combined = f"{c} {t}"

    # FI-Infra é uma estratégia própria e prevalece sobre FIA/FII e crédito.
    if any(x in combined for x in [
        "FI INFRA", "FI-INFRA", "FIINFRA", "FIC FI INFRA", "FIC FI-INFRA",
        "FIC INFR", "FUNDO INCENTIVADO DE INFRA", "DEBENTURES INCENTIVADAS"
    ]):
        return "RF Brasil", "FiInfra e Cetipados"

    # Internacional precisa ser identificado antes de palavras como AÇÕES/FIA.
    if any(x in combined for x in [
        "EXTERIOR", "INTERNACIONAL", "GLOBAL", "OFFSHORE", "DOLAR", "CAMBIAL", "USD"
    ]):
        if any(x in combined for x in ["BOND", "FIXED", "RENDA FIXA", "TREASURY", "CREDIT"]):
            return "Internacional", "Renda Fixa Internacional"
        return "Internacional", "Renda Variável Internacional"

    if has_any_phrase(combined, ["FII", "IMOBILIARIO", "REAL ESTATE"]):
        return "RV Brasil", "FIIs"
    if has_any_phrase(combined, ["ACOES", "RENDA VARIAVEL", "RV BRASIL", "SMALL CAPS", "IBOV", "LONG BIASED", "FIA"]):
        return "RV Brasil", "Ações"

    # FIDC, fundos de crédito, FIRF, DI, multimercado etc. entram pela liquidez.
    fund_rf_terms = [
        "FIDC", "DIREITOS CREDITORIOS", "CREDITO PRIVADO", "HIGH GRADE", "HIGH YIELD",
        "RENDA FIXA", "REFERENCIADO DI", "CDI", "FIRF", "MULTIMERCADO", "FIM",
        "MACRO", "RETORNO ABSOLUTO", "IPCA", "IMA-B", "PREFIXADO", "IRF-M"
    ]
    if has_any_phrase(combined, fund_rf_terms):
        return "RF Brasil", bucket_from_liquidity_days(liquidez_days)

    # Fundo sem classificação reconhecida: usa liquidez se disponível.
    if pd.notna(liquidez_days):
        return "RF Brasil", bucket_from_liquidity_days(liquidez_days)
    return "Fora da Estratégia", "Fundos de Investimento / Sem Liquidez Mapeada"


def infer_liquidity_days_from_row(row: pd.Series) -> float:
    """Hierarquia de liquidez: override > base manual > dados da corretora > nome.

    A base curada deve prevalecer sobre campos brutos da corretora, pois contempla
    exceções operacionais como fundos cetipados sem liquidez efetiva.
    """
    override = liquidity_override_for_row(row)
    if pd.notna(override): return float(override)
    manual = pd.to_numeric(row.get("manual_liquidez", np.nan), errors="coerce")
    if pd.notna(manual): return float(manual)
    cot = parse_days_from_text(row.get("cotizacao_resgate", ""))
    liq = parse_days_from_text(row.get("liquidacao_resgate", ""))
    if pd.notna(cot) or pd.notna(liq): return (0 if pd.isna(cot) else cot) + (0 if pd.isna(liq) else liq)
    direct = parse_days_from_text(row.get("liquidez", ""))
    if pd.notna(direct): return float(direct)
    for v in [row.get("asset_nome", ""), row.get("asset_id", "")]:
        d = parse_days_from_text(v)
        if pd.notna(d): return float(d)
    return np.nan

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
    cnpj = only_digits_str(row.get("cnpj", ""))
    text = " ".join([asset_id, nome, asset_tipo, mercado, sub_mercado, estrategia, indexador, liquidez, emissor, taxa])
    # Flags robustas de indexador. Alguns relatórios trazem prefixado como
    # "CDB PRE DU", "CRA PRE DU" ou apenas "PRE" no nome, sem escrever
    # "prefixado". Usamos regex com borda de palavra para não confundir com
    # PREV/PREVIDÊNCIA.
    has_ipca = bool(re.search(r"\b(IPCA|IPC\s*-?\s*A|INFLACAO|INFLAÇÃO)\b", text))
    has_cdi = bool(re.search(r"\b(CDI|DI|POS\s*-?\s*FIXADO|PÓS\s*-?\s*FIXADO|POS\s+FIXADO|PÓS\s+FIXADO)\b", text))
    has_pre = bool(re.search(r"\b(PRE|PRÉ|PREFIXADO)\b|PRE\s*-\s*FIXADO|PRÉ\s*-\s*FIXADO|PRE\s+FIXADO|PRÉ\s+FIXADO", text))

    # Sinais vindos diretamente dos relatórios.
    # XP: no relatório de Renda Fixa, prefixados normalmente vêm com NomeIndexador vazio
    # e TaxaCompleta como '+13,10%' / '+14,50%', enquanto CDI/IPCA aparecem no NomeIndexador
    # ou na TaxaCompleta.
    # BTG: a coluna Estratégia costuma trazer Pré-fixado/Pós-fixado/Inflação; quando vem
    # em branco, a Taxa Compra/Taxa Emissão ajuda no fallback.
    indexador_blank = indexador in ["", "NAN", "NONE", "NULL", "-", "0"]
    estrategia_blank = estrategia in ["", "NAN", "NONE", "NULL", "-"]
    taxa_clean = taxa.replace("%", "").replace("+", "").replace(",", ".").strip()
    try:
        taxa_num = float(re.sub(r"[^0-9.\-]", "", taxa_clean)) if taxa_clean else np.nan
    except Exception:
        taxa_num = np.nan
    has_taxa_nominal = pd.notna(taxa_num) and abs(float(taxa_num)) > 0.0001 and not has_cdi and not has_ipca
    pre_by_report = (has_pre or (indexador_blank and has_taxa_nominal and ("RENDA FIXA" in mercado or "RENDA FIXA" in asset_tipo or asset_id.startswith(("CDB", "LCI", "LCA", "LCD", "LF", "DEB", "CRI", "CRA", "CDCA")))))
    liq_days = infer_liquidity_days_from_row(row)

    # Exceção operacional cadastrada no próprio Nome fundos e prev.xlsx.
    op_classe = str(row.get("manual_classe_operacional", "") or "").strip()
    op_bucket = str(row.get("manual_subbucket_operacional", "") or "").strip()
    if op_classe and op_bucket:
        return pd.Series([op_classe, op_bucket, op_bucket, "Override operacional / base de fundos"])

    # Eventos não são ativos fora da estratégia. Proventos ficam como caixa a
    # receber e códigos final 12 são direitos de subscrição monitorados em RV.
    if asset_tipo in ["PROVENTOS", "PROVENTOS FUNDO IMOB"] or "PROVENTO" in asset_tipo:
        return pd.Series(["Caixa", "Proventos a Receber", "Proventos a Receber", "Evento / não rebalancear"])
    if re.fullmatch(r"[A-Z]{4}12", asset_id):
        return pd.Series(["RV Brasil", "Direitos de Subscrição", "Direitos de Subscrição", "Direito / não rebalancear"])

    if bool(row.get("saldo_operacional", False)):
        if corretora == "CS":
            return pd.Series(["Internacional", "Caixa Internacional", "Caixa Internacional", "Operacional"])
        return pd.Series(["Caixa", "Saldo em Conta", "Saldo em Conta", "Operacional"])

    # A natureza econômica prevalece inclusive quando a corretora entrega o
    # ativo em uma aba incorreta, como debênture dentro de Renda Variável.
    early_is_fiinfra = (
        asset_id in FI_INFRA_TICKERS
        or any(x in text for x in ["FI INFRA", "FI-INFRA", "FIINFRA", "FIC FI INFRA", "FIC FI-INFRA", "FIC INFR"])
    )
    if early_is_fiinfra:
        return pd.Series(["RF Brasil", "FiInfra e Cetipados", "FiInfra e Cetipados", "Natureza econômica / FI-Infra"])

    early_looks_like_fund = has_any_phrase(text, ["FIC", "FIM", "FIRF", "FIA", "FIDC", "FUNDO", "FUNDOS", "FIF"])
    early_credit_title = bool(re.search(r"\b(DEB|DEBENTURE|DEBENTURES|CRI|CRA|CDCA)\b", text))
    if early_credit_title and not early_looks_like_fund:
        return pd.Series(["RF Brasil", "Crédito Privado", "Crédito Privado", "Natureza econômica / Crédito Privado"])

    # Fundo também prevalece sobre a aba de origem. Isso corrige fundos
    # internacionais e FIDC eventualmente entregues na aba de ações.
    if early_looks_like_fund:
        classe_base = row.get("manual_classe", "") if row.get("manual_match", False) else text
        classe, bucket = classify_fund_class(classe_base, text, liq_days)
        origem = "Manual/Fundos" if row.get("manual_match", False) else "Heurística de fundo"
        if asset_tipo in ["PREVIDENCIA", "PREVIDÊNCIA"] or any(x in text for x in ["PREVIDENCIA", "PGBL", "VGBL"]):
            origem = "Previdência"
        return pd.Series([classe, bucket, bucket, origem])

    # BTG: Mercado/Sub Mercado/Estratégia normalmente já dizem exatamente o direcionamento.
    if corretora == "BTG":
        if mercado == "CONTA CORRENTE" or sub_mercado == "CC":
            return pd.Series(["Caixa", "Saldo em Conta", "Saldo em Conta", "Operacional"])
        if mercado == "PREVIDENCIA" or sub_mercado == "PP":
            classe, bucket = classify_fund_class(row.get("manual_classe", "") or estrategia, text, liq_days)
            return pd.Series([classe, bucket, bucket, "Previdência"])
        if mercado == "RENDA FIXA":
            if sub_mercado in ["TESOURO DIRETO", "TITULO PUBLICO", "TÍTULO PÚBLICO"]:
                if any(x in estrategia + " " + text for x in ["INFLACAO", "INFLAÇÃO", "IPCA", "NTNB", "NTN-B"]):
                    return pd.Series(["RF Brasil", "Inflação - Tesouro", "Inflação - Tesouro", "BTG"])
                if any(x in estrategia + " " + text for x in ["PRE", "PRÉ", "PREFIXADO", "LTN", "NTN-F"]):
                    return pd.Series(["RF Brasil", "Pré - Tesouro", "Pré - Tesouro", "BTG"])
                return pd.Series(["RF Brasil", "Pós - Imediato", "Pós - Imediato", "BTG"])
            # Fora de títulos bancários e públicos, toda renda fixa direta é crédito privado.
            bank_title = bool(re.match(r"^(CDB|LCI|LCA|LCD|LF)\b", asset_id)) or any(
                x in sub_mercado for x in ["BANCARIO", "BANCÁRIO"]
            )
            if not bank_title:
                return pd.Series(["RF Brasil", "Crédito Privado", "Crédito Privado", "BTG / título de crédito privado"])
            if "INFL" in estrategia or has_ipca:
                return pd.Series(["RF Brasil", "Inflação - Bancário", "Inflação - Bancário", "BTG Estratégia/Indexador"])
            if "PRE" in estrategia or "PRÉ" in estrategia or pre_by_report or (estrategia_blank and has_taxa_nominal):
                return pd.Series(["RF Brasil", "Pré - Bancário", "Pré - Bancário", "BTG Estratégia/Taxa"])
            return pd.Series(["RF Brasil", "Pós - 361+ dias", "Pós - 361+ dias", "BTG Estratégia/Indexador"])
        if mercado == "FUNDOS":
            classe, bucket = classify_fund_class(row.get("manual_classe", "") or estrategia, text, liq_days)
            return pd.Series([classe, bucket, bucket, "Manual" if row.get("manual_match", False) else "BTG/Heurística"])
        if mercado == "RENDA VARIAVEL" or mercado == "RENDA VARIÁVEL":
            if asset_id in ATIVOS_ESTRATEGICOS_B3:
                info = ATIVOS_ESTRATEGICOS_B3[asset_id]
                return pd.Series([info["classe"], info["subbucket"], info["subbucket"], f"ETF B3: {info['estrategia']}"])
            if sub_mercado == "FII" or asset_id.endswith("11"):
                # FiInfra antes de FII comum.
                if asset_id in FI_INFRA_TICKERS:
                    return pd.Series(["RF Brasil", "FiInfra e Cetipados", "FiInfra e Cetipados", "Estratégia"])
                return pd.Series(["RV Brasil", "FIIs", "FIIs", "Estratégia"])
            if sub_mercado == "ACAO" or len(asset_id) in [5, 6]:
                return pd.Series(["RV Brasil", "Ações", "Ações", "Estratégia"])
        if mercado == "COE":
            return pd.Series(["Fora da Estratégia", "COE / Estruturados", "COE / Estruturados", "Fora da Estratégia"])

    # Natureza econômica prevalece sobre a aba de origem do relatório.
    # Isso corrige ativos que chegam em abas inadequadas (ex.: debênture em Ações)
    # e fundos de infraestrutura cujo nome também contém FIA/FII/ações.
    is_fiinfra = (
        asset_id in FI_INFRA_TICKERS
        or any(x in text for x in ["FI INFRA", "FI-INFRA", "FIINFRA", "FIC FI INFRA", "FIC FI-INFRA", "FIC INFR", "FUNDO INCENTIVADO DE INFRA", "DEBENTURES INCENTIVADAS", "DEBÊNTURES INCENTIVADAS"])
    )
    if is_fiinfra:
        return pd.Series(["RF Brasil", "FiInfra e Cetipados", "FiInfra e Cetipados", "Natureza econômica / FI-Infra"])

    is_credit_title = bool(re.search(r"\b(DEB|DEBENTURE|DEBENTURES|DEBÊNTURE|DEBÊNTURES|CRI|CRA|CDCA)\b", text))
    looks_like_fund = has_any_phrase(text, ["FIC", "FIM", "FIRF", "FIA", "FIDC", "FUNDO", "FUNDOS", "FIF"])
    if is_credit_title and not looks_like_fund:
        return pd.Series(["RF Brasil", "Crédito Privado", "Crédito Privado", "Natureza econômica / Crédito Privado"])

    # XP: fundos e previdência têm CNPJ/prazos na própria planilha e/ou na base Nome fundos e prev.
    if asset_tipo in ["FUNDOS", "PREVIDENCIA", "PREVIDÊNCIA"] or has_any_phrase(text, ["FIC", "FIM", "FIRF", "FIA", "FIDC", "FUNDO", "FUNDOS", "FIF"]):
        classe_base = row.get("manual_classe", "") if row.get("manual_match", False) else ""
        if not classe_base:
            classe_base = text
        classe, bucket = classify_fund_class(classe_base, text, liq_days)
        origem = "Manual/Fundos" if row.get("manual_match", False) else "XP/Heurística"
        if asset_tipo in ["PREVIDENCIA", "PREVIDÊNCIA"] or any(x in text for x in ["PREV", "PREVIDENCIA", "PREVIDÊNCIA", "PGBL", "VGBL"]):
            # Mantém o recurso dentro da classe econômica do fundo, mas marca a origem como previdência.
            return pd.Series([classe, bucket, bucket, "Previdência"])
        return pd.Series([classe, bucket, bucket, origem])

    # Internacional
    if corretora == "CS":
        if any(x in text for x in ["CASH", "MONEY MARKET", "SWEEP", "BANK DEPOSIT"]):
            return pd.Series(["Internacional", "Caixa Internacional", "Caixa Internacional", "Operacional"])
        if any(x in text for x in ["BOND", "FIXED", "TREASURY", "CD ", "CERTIFICATE", "NOTE", "CORPORATE"]):
            return pd.Series(["Internacional", "Renda Fixa Internacional", "Renda Fixa Internacional", "Estratégia"])
        return pd.Series(["Internacional", "Renda Variável Internacional", "Renda Variável Internacional", "Estratégia"])

    # Caixa / saldo fallback conservador
    if any(x in text for x in ["SALDO FINANCEIRO", "CONTA CORRENTE", "VALORDISPONIVEL"]):
        return pd.Series(["Caixa", "Saldo em Conta", "Saldo em Conta", "Operacional"])

    # COE / estruturados
    if has_any_phrase(text, ["COE", "ESTRUTURADO", "OPCOES FLEX", "OPCAO FLEX", "OPCOES", "OPÇÃO"]):
        return pd.Series(["Fora da Estratégia", "COE / Estruturados", "COE / Estruturados", "Fora da Estratégia"])

    # Ativos estratégicos negociados na B3 que não podem cair na regra genérica de FII.
    if asset_id in ATIVOS_ESTRATEGICOS_B3:
        info = ATIVOS_ESTRATEGICOS_B3[asset_id]
        return pd.Series([info["classe"], info["subbucket"], info["subbucket"], f"ETF B3: {info['estrategia']}"])

    # Fi-Infra: antes de FII, porque todos terminam em 11.
    if asset_id in FI_INFRA_TICKERS or any(x in text for x in ["FI INFRA", "FI-INFRA", "FIINFRA", "FIC FI INFRA", "FIC FI-INFRA", "FIC INFR", "DEB INCENTIVADA"]):
        return pd.Series(["RF Brasil", "FiInfra e Cetipados", "FiInfra e Cetipados", "Estratégia"])

    # Bolsa Brasil
    if asset_tipo in ["FUNDOS IMOBILIARIOS", "FUNDOS IMOBILIÁRIOS"] or has_any_phrase(text, ["FUNDO IMOB", "FII", "FUNDO IMOBILIARIO"]):
        return pd.Series(["RV Brasil", "FIIs", "FIIs", "Estratégia"])
    if (asset_id.endswith("11") and len(asset_id) >= 5 and asset_id[:4].isalpha()):
        return pd.Series(["RV Brasil", "FIIs", "FIIs", "Estratégia"])
    if asset_tipo in ["ACOES", "AÇÕES"] or has_any_phrase(text, ["ACAO", "AÇÃO", "BOVESPA", "RENDA VARIAVEL"]):
        return pd.Series(["RV Brasil", "Ações", "Ações", "Estratégia"])
    if len(asset_id) in [5, 6] and asset_id[:4].isalpha() and asset_id[-1].isdigit():
        return pd.Series(["RV Brasil", "Ações", "Ações", "Estratégia"])

    # Tesouro / títulos públicos são Tesouro na estratégia.
    if any(x in text for x in ["TESOURO SELIC", "LFT", "SELIC"]):
        return pd.Series(["RF Brasil", "Pós - Imediato", "Pós - Imediato", "Estratégia"])
    if any(x in text for x in ["TESOURO PRE", "NTN-F", "NTNF", "LTN"]):
        return pd.Series(["RF Brasil", "Pré - Tesouro", "Pré - Tesouro", "Estratégia"])
    if any(x in text for x in ["TESOURO IPCA", "NTN-B", "NTNB", "NTNB PRINC"]):
        return pd.Series(["RF Brasil", "Inflação - Tesouro", "Inflação - Tesouro", "Estratégia"])

    # Renda fixa local por indexador/taxa.
    is_rf_local = ("RENDA FIXA" in mercado or "RENDA FIXA" in asset_tipo or asset_id.startswith(("CDB", "LCI", "LCA", "LCD", "LF", "DEB", "CRI", "CRA", "CDCA")))
    if is_rf_local or has_any_phrase(text, ["CDB", "LCI", "LCA", "LCD", "LF", "CRI", "CRA", "DEB", "DEBENTURE", "CDCA"]):
        bank_title = asset_id.startswith(("CDB", "LCI", "LCA", "LCD", "LF"))
        if not bank_title:
            return pd.Series(["RF Brasil", "Crédito Privado", "Crédito Privado", "Título privado não bancário"])
        if has_ipca:
            return pd.Series(["RF Brasil", "Inflação - Bancário", "Inflação - Bancário", "Relatório: indexador/taxa"])
        if pre_by_report:
            return pd.Series(["RF Brasil", "Pré - Bancário", "Pré - Bancário", "Relatório: indexador/taxa"])
        return pd.Series(["RF Brasil", "Pós - 361+ dias", "Pós - 361+ dias", "Relatório: indexador/taxa"])

    return pd.Series(["Fora da Estratégia", "Outros / Não Classificado", "Outros / Não Classificado", "Revisar"])
@st.cache_data(show_spinner=False)
def enrich_positions_cached(df: pd.DataFrame, mapping_signature: tuple = ()) -> pd.DataFrame:
    df = df.copy()
    df = df.loc[:, ~df.columns.duplicated()].copy()
    df = df.drop(columns=[c for c in ["classe_macro", "subclasse", "subbucket", "tratamento"] if c in df.columns], errors="ignore")
    for c in ["valor_mercado", "quantidade", "valor_original"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df = apply_manual_fund_mapping(df)
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



def rv_recommendation(pos_cliente: pd.DataFrame, p: dict[str, float], pl: float, modelo: str, price_ref: dict[str, float] | None = None) -> pd.DataFrame:
    price_ref = price_ref or {}
    rows = []
    universe_by_bucket = rv_universe(modelo)
    model_tickers = {ticker_clean(t) for xs in universe_by_bucket.values() for t in xs}
    for bucket, tickers in universe_by_bucket.items():
        alvo_total = pl * peso_get(p, bucket)
        alvo_ativo = alvo_total / len(tickers) if tickers else 0
        for t in tickers:
            preco, qtd, atual = current_exchange_position(pos_cliente, t, price_ref)
            qtd_ideal = round(alvo_ativo / preco) if pd.notna(preco) and preco > 0 else np.nan
            qtd_operar = qtd_ideal - qtd if pd.notna(qtd_ideal) else np.nan
            diff = alvo_ativo - atual if pd.notna(atual) else np.nan
            rows.append([t, preco, qtd, atual, qtd_ideal, alvo_ativo, diff, qtd_operar, bucket])

    bolsa = pos_cliente[pos_cliente["subbucket"].isin(["Ações", "FIIs"]) & exchange_position_mask(pos_cliente)].copy()
    for tk, grp in bolsa.groupby("ticker_norm"):
        if not tk or tk in model_tickers:
            continue
        ativo = str(grp["asset_id"].iloc[0])
        preco, qtd, atual = current_exchange_position(pos_cliente, tk, price_ref)
        diff = -atual if pd.notna(atual) else np.nan
        qtd_operar = -qtd if pd.notna(preco) and preco > 0 else np.nan
        rows.append([ativo, preco, qtd, atual, 0, 0.0, diff, qtd_operar, "Fora do modelo"])
    return pd.DataFrame(rows, columns=["Ativo", "Preço referência", "Qtd Atual", "Valor Atual", "Qtd Ideal", "Valor Ideal", "Diferença", "Qtd a operar", "Grupo"])


def fiinfra_recommendation(pos_cliente: pd.DataFrame, p: dict[str, float], pl: float, price_ref: dict[str, float] | None = None) -> pd.DataFrame:
    price_ref = price_ref or {}
    rows = []
    model_tickers = {ticker_clean(t) for t in FI_INFRA_TICKERS}
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
            preco, qtd, atual = current_exchange_position(pos_cliente, t, price_ref)
            qtd_ideal = round(alvo_ativo / preco) if pd.notna(preco) and preco > 0 else np.nan
            qtd_operar = qtd_ideal - qtd if pd.notna(qtd_ideal) else np.nan
            diff = alvo_ativo - atual if pd.notna(atual) else np.nan
            rows.append([t, preco, qtd, atual, qtd_ideal, alvo_ativo, diff, qtd_operar, nome])

    held = pos_cliente[pos_cliente["subbucket"].eq("FiInfra e Cetipados") & exchange_position_mask(pos_cliente)].copy()
    for tk, grp in held.groupby("ticker_norm"):
        if not tk or tk in model_tickers:
            continue
        ativo = str(grp["asset_id"].iloc[0])
        preco, qtd, atual = current_exchange_position(pos_cliente, tk, price_ref)
        diff = -atual if pd.notna(atual) else np.nan
        qtd_operar = -qtd if pd.notna(preco) and preco > 0 else np.nan
        rows.append([ativo, preco, qtd, atual, 0, 0.0, diff, qtd_operar, "Fora do modelo"])
    return pd.DataFrame(rows, columns=["Ativo", "Preço referência", "Qtd Atual", "Valor Atual", "Qtd Ideal", "Valor Ideal", "Diferença", "Qtd a operar", "Grupo"])

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


def mapping_files_signature() -> tuple:
    """Invalida o enriquecimento quando as bases de fundos/manual forem trocadas."""
    fp = find_file("Nome fundos e prev.xlsx")
    mp = find_file("Manual de Alocação.xlsx")
    return (*file_cache_signature(fp), *file_cache_signature(mp))


# =============================================================================
# Layout global
# =============================================================================
pesos = load_pesos_xlsx(str(find_file("Pesos-alocacao.xlsx")))
df_contas = load_contas_cached()

lp = logo_path()
if lp:
    h_logo, h_title = st.columns([1.15, 5.85], vertical_alignment="center")
    with h_logo:
        st.image(str(lp), width=250)
    with h_title:
        st.markdown('<h1 class="mw-title">Balanceamento de Carteiras</h1>', unsafe_allow_html=True)
        st.markdown(f'<div class="mw-version">M Wealth • Versão {APP_VERSION}</div>', unsafe_allow_html=True)
else:
    st.markdown('<h1 class="mw-title">M Wealth - Balanceamento de Carteiras</h1>', unsafe_allow_html=True)
    st.markdown(f'<div class="mw-version">Versão {APP_VERSION}</div>', unsafe_allow_html=True)

page = st.segmented_control(
    "",
    ["Controle de Saldo", "Asset Allocation", "Carteira Teórica"],
    default="Controle de Saldo",
)
if page is None:
    page = "Controle de Saldo"

st.markdown('<div class="mw-line"></div>', unsafe_allow_html=True)

# =============================================================================
# Página 1 - Controle de saldo
# =============================================================================
if page == "Controle de Saldo":
    st.header("Controle de Saldo para Operação")

    c0, c1, c2 = st.columns([1.0, 1.35, 4.2], vertical_alignment="bottom")
    force = c0.button("Atualizar base", type="primary", use_container_width=True)
    if force:
        st.cache_data.clear()
    min_saldo_txt = c1.text_input("Saldo mínimo", value=format_brl(10000.0), help="Digite no formato financeiro. Ex.: R$ 10.000,00")
    min_saldo = parse_brl_input(min_saldo_txt, 10000.0)

    try:
        df_latest, meta, mode = load_positions_cached(force_rebuild=force)
        df_latest = enrich_positions_cached(df_latest, mapping_files_signature())
        df_latest = ensure_saldo_operacional(df_latest)
    except Exception as e:
        st.error(f"Falha ao carregar/consolidar posições: {e}")
        st.stop()

    st.caption(f"Última consolidação: **{meta.get('built_at', 'n/d')}**")

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
    with k1:
        metric_card("Contas com saldo acima do mínimo", f"{int((painel['Saldo'] >= min_saldo).sum())}")
    with k2:
        metric_card("Saldo total disponível", format_brl(painel.loc[painel["Saldo"] > 0, "Saldo"].sum()))
    with k3:
        metric_card("Caixa negativo", format_brl(painel.loc[painel["Saldo"] < 0, "Saldo"].sum()))

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
        df_latest = enrich_positions_cached(df_latest, mapping_files_signature())
        df_latest = ensure_saldo_operacional(df_latest)
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

    # Marca somente as posições reais de bolsa pelo preço atual do Yahoo Finance.
    # O PL, os valores atuais e as diferenças passam a refletir quantidade × cotação atual.
    held_tickers = pos_cliente.loc[exchange_position_mask(pos_cliente), "ticker_norm"].dropna().astype(str).tolist()
    model_tickers = [t for xs in rv_universe(modelo).values() for t in xs] + FI_INFRA_TICKERS
    quote_tickers = tuple(sorted({ticker_clean(t) for t in held_tickers + model_tickers if ticker_clean(t)}))
    price_ref = load_yfinance_prices(quote_tickers)
    pos_cliente = mark_exchange_positions_to_market(pos_cliente, price_ref)

    pl = float(pos_cliente["valor_mercado"].sum())
    macro_df, sub_df = portfolio_tables(pos_cliente, p, pl)

    pl_xp = float(pos_cliente.loc[pos_cliente["corretora"].eq("XP"), "valor_mercado"].sum())
    pl_btg = float(pos_cliente.loc[pos_cliente["corretora"].eq("BTG"), "valor_mercado"].sum())
    pl_cs = float(pos_cliente.loc[pos_cliente["corretora"].eq("CS"), "valor_mercado"].sum())
    saldo = float(pos_cliente.loc[pos_cliente.get("saldo_operacional", False).fillna(False).astype(bool), "valor_mercado"].sum()) if "saldo_operacional" in pos_cliente.columns else float(pos_cliente.loc[pos_cliente["subbucket"].eq("Saldo em Conta"), "valor_mercado"].sum())
    manual_matches = int(pos_cliente.get("manual_match", pd.Series([False] * len(pos_cliente), index=pos_cliente.index)).fillna(False).astype(bool).sum())
    nao_class = float(pos_cliente.loc[pos_cliente["subbucket"].eq("Outros / Não Classificado"), "valor_mercado"].sum())

    k1, k2, k3, k4, k5 = st.columns(5)
    with k1:
        metric_card("PL Total", format_brl(pl))
    with k2:
        metric_card("XP", format_brl(pl_xp))
    with k3:
        metric_card("BTG", format_brl(pl_btg))
    with k4:
        metric_card("CS", format_brl(pl_cs))
    with k5:
        metric_card("Saldo", format_brl(saldo))
    st.caption(f"Perfil: **{perfil_cliente}** • Modelo aplicado: **{modelo}** • Fundos reconhecidos pelo manual: **{manual_matches}** • Não classificado: **{format_brl(nao_class)}**")

    st.markdown('<div class="mw-line"></div>', unsafe_allow_html=True)

    st.subheader("Visão macro atual x ideal")
    macro_view = macro_df.copy()
    macro_view["Classe"] = macro_view["Classe"].apply(friendly_class_name)
    macro_view = macro_view.drop(columns=["Ação", "Status"], errors="ignore")

    macro_hier = macro_hierarchy_table(sub_df, pl)
    col1, col2 = st.columns([0.95, 2.45])
    with col1:
        # Mesmo conceito da carteira teórica: gráfico pela abertura das estratégias,
        # não apenas pelo macro Renda Fixa / Renda Variável / Internacional.
        plot = macro_hier.copy()
        plot = plot[(~plot.get("_header", False).astype(bool)) & (plot["Quanto tem"] > 0)].copy()
        plot["Estratégia"] = plot["Estratégia"].astype(str).str.strip()
        if plot.empty:
            plot = macro_df[macro_df["Valor Atual"] > 0].copy()
            plot["Estratégia"] = plot["Classe"].apply(friendly_class_name)
            plot = plot.rename(columns={"Valor Atual": "Quanto tem"})
        fig = px.pie(plot, names="Estratégia", values="Quanto tem", title="Distribuição atual por estratégia", hole=.48)
        fig.update_layout(height=365, margin=dict(l=8, r=8, t=45, b=8), showlegend=True, legend_title_text="")
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        st.dataframe(
            macro_hierarchy_styler(macro_hier),
            use_container_width=True,
            hide_index=True,
            height=min(820, max(420, 37 * (len(macro_hier) + 1))),
        )

    st.subheader("Abertura por estratégia")
    st.markdown('<div class="mw-muted">Abertura objetiva por classe. A diferença positiva indica valor a alocar; diferença negativa indica excesso.</div>', unsafe_allow_html=True)
    class_order = ["RF Brasil", "Alternativos"]
    for classe in class_order:
        class_df = sub_df[sub_df["Classe"].eq(classe)].copy()
        if class_df.empty:
            continue
        valor_atual_cls = float(class_df["Valor Atual"].sum())
        valor_ideal_cls = float(class_df["Valor Ideal"].sum())
        diff_cls = float(class_df["Diferença"].sum())
        titulo = f"{friendly_class_name(classe)} • Atual {format_brl_label(valor_atual_cls)} | Ideal {format_brl_label(valor_ideal_cls)} | Diferença {format_brl_label(diff_cls)}"
        expanded = classe == "RF Brasil" or abs(diff_cls) > 300
        with st.expander(titulo, expanded=expanded):
            class_view = class_df.copy()
            class_view["Estratégia"] = class_view["Subbucket"].apply(friendly_strategy_name)
            class_view = class_view[["Estratégia", "Peso Atual", "Peso Ideal", "Valor Atual", "Valor Ideal", "Diferença"]]
            st.dataframe(
                money_color_styler(
                    class_view,
                    money_cols=["Valor Atual", "Valor Ideal", "Diferença"],
                    pct_cols=["Peso Atual", "Peso Ideal"],
                    diff_cols=["Diferença"],
                ),
                use_container_width=True,
                hide_index=True,
            )

            ativos_cls = pos_cliente[pos_cliente["classe_macro"].eq(classe)].copy()
            if not ativos_cls.empty:
                cols_pos = ["subbucket", "asset_id", "asset_nome", "corretora", "valor_mercado", "quantidade", "manual_fundo", "manual_liquidez", "tratamento"]
                ativos_cls = ativos_cls[[c for c in cols_pos if c in ativos_cls.columns]].sort_values("valor_mercado", ascending=False).head(80)
                ativos_cls["subbucket"] = ativos_cls["subbucket"].apply(friendly_strategy_name)
                ativos_cls = ativos_cls.rename(columns={
                    "subbucket": "Estratégia",
                    "asset_id": "Ativo",
                    "asset_nome": "Nome",
                    "corretora": "Corretora",
                    "valor_mercado": "Valor",
                    "quantidade": "Quantidade",
                    "manual_fundo": "Fundo no manual",
                    "manual_liquidez": "Liquidez D+",
                    "tratamento": "Origem do match",
                })
                with st.expander("Ver ativos classificados nessa classe", expanded=False):
                    st.dataframe(
                        prepare_display(ativos_cls, money_cols=["Valor"], qty_cols=["Quantidade"], max_rows=80),
                        use_container_width=True,
                        hide_index=True,
                    )

    st.subheader("Produtos de bolsa e infraestrutura")
    if not HAS_YFINANCE:
        st.error("A biblioteca yfinance não está instalada. Instale-a para habilitar preços atuais e cálculos de ordens.")
    missing_quotes = sorted(set(quote_tickers) - set(price_ref))
    if missing_quotes:
        st.warning(
            "Sem cotação do Yahoo Finance para: " + ", ".join(missing_quotes)
            + ". Esses ativos ficarão sem recomendação de quantidade até a cotação estar disponível."
        )
    else:
        st.caption("Preços atualizados pelo Yahoo Finance, com cache de 5 minutos. Valor atual = quantidade real × preço de mercado.")
    rv_df = rv_recommendation(pos_cliente, p, pl, modelo, price_ref)
    fi_df = fiinfra_recommendation(pos_cliente, p, pl, price_ref)
    tab_a, tab_b = st.tabs(["Ações e Fundos Imobiliários", "Infraestrutura"])
    with tab_a:
        rv_view = rv_df[rv_df["Valor Ideal"].gt(0) | rv_df["Valor Atual"].gt(0)].copy()
        rv_table = rv_view.drop(columns=["Grupo"], errors="ignore")
        if rv_view.empty:
            st.info("Este modelo não possui alvo para ações ou fundos imobiliários.")
        else:
            st.dataframe(
                money_color_styler(
                    rv_table,
                    money_cols=["Preço referência", "Valor Atual", "Valor Ideal", "Diferença"],
                    qty_cols=["Qtd Atual", "Qtd Ideal", "Qtd a operar"],
                    diff_cols=["Diferença", "Qtd a operar"],
                ),
                use_container_width=True,
                hide_index=True,
            )
            basket = basket_excel_bytes(rv_view, grupo_sel)
            st.download_button(
                "Baixar basket de ações e fundos imobiliários",
                data=basket,
                file_name=f"basket_acoes_fiis_{str(grupo_sel).replace(' ', '_').lower()}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
    with tab_b:
        fi_view = fi_df[fi_df["Valor Ideal"].gt(0) | fi_df["Valor Atual"].gt(0)].copy()
        fi_table = fi_view.drop(columns=["Grupo"], errors="ignore")
        if fi_view.empty:
            st.info("Este modelo não possui alvo para infraestrutura.")
        else:
            st.dataframe(
                money_color_styler(
                    fi_table,
                    money_cols=["Preço referência", "Valor Atual", "Valor Ideal", "Diferença"],
                    qty_cols=["Qtd Atual", "Qtd Ideal", "Qtd a operar"],
                    diff_cols=["Diferença", "Qtd a operar"],
                ),
                use_container_width=True,
                hide_index=True,
            )
            basket = basket_excel_bytes(fi_view, grupo_sel)
            st.download_button(
                "Baixar basket de infraestrutura",
                data=basket,
                file_name=f"basket_infra_{str(grupo_sel).replace(' ', '_').lower()}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

    st.subheader("Detalhamento das posições")
    buckets = [b for b in SUBBUCKET_ORDER if b in set(pos_cliente["subbucket"])]
    bucket_options = {friendly_strategy_name(b): b for b in buckets}
    if bucket_options:
        bucket_label = st.selectbox("Abrir posições por estratégia", list(bucket_options.keys()))
        bucket_sel = bucket_options[bucket_label]
        subpos = pos_cliente[pos_cliente["subbucket"].eq(bucket_sel)].copy()
        agrup = subpos.groupby(["asset_id", "asset_nome", "corretora", "subbucket"], dropna=False, as_index=False).agg(Valor=("valor_mercado", "sum"), Quantidade=("quantidade", "sum"), Contas=("conta", "nunique")).sort_values("Valor", ascending=False)
        agrup["Peso no Cliente"] = np.where(pl > 0, agrup["Valor"] / pl, 0)
        agrup["Estratégia"] = agrup["subbucket"].apply(friendly_strategy_name)
        agrup = agrup.rename(columns={"asset_id": "Ativo", "asset_nome": "Nome", "corretora": "Corretora"})
        st.dataframe(
            prepare_display(agrup[["Estratégia", "Ativo", "Nome", "Corretora", "Valor", "Quantidade", "Peso no Cliente", "Contas"]], money_cols=["Valor"], pct_cols=["Peso no Cliente"], qty_cols=["Quantidade"], max_rows=600),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("Nenhuma estratégia encontrada para detalhamento.")

    with st.expander("Revisões e exceções", expanded=False):
        universo = set(ACOES_SEM_RENDA + ACOES_COM_RENDA + FIIS_RECOMENDADOS + FI_INFRA_TICKERS + list(ATIVOS_ESTRATEGICOS_B3.keys()))
        fora_df = pos_cliente[
            pos_cliente["classe_macro"].eq("Fora da Estratégia") |
            ((pos_cliente["classe_macro"].eq("RV Brasil")) & (~pos_cliente["ticker_norm"].isin({ticker_clean(x) for x in universo}))) |
            (pos_cliente["subbucket"].str.contains("Sem Liquidez|Não Classificado|COE|Previdência", case=False, na=False))
        ].sort_values("valor_mercado", ascending=False)
        cols = ["corretora", "conta", "CLIENTE", "asset_id", "asset_nome", "classe_macro", "subbucket", "tratamento", "manual_fundo", "manual_classe", "manual_liquidez", "manual_metodo", "manual_score", "valor_mercado", "quantidade", "indexador", "liquidez", "vencimento"]
        view = fora_df[[c for c in cols if c in fora_df.columns]].copy()
        for c in ["classe_macro", "subbucket"]:
            if c in view.columns:
                view[c] = view[c].apply(friendly_class_name if c == "classe_macro" else friendly_strategy_name)
        st.dataframe(prepare_display(view, money_cols=["valor_mercado"], qty_cols=["quantidade"], max_rows=500), use_container_width=True, hide_index=True)


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
