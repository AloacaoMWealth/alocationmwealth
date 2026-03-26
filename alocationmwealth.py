import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
from datetime import datetime, timedelta
import requests
import json
from pathlib import Path
import locale
import positions as posmod  # seu módulo positions.py


locale.setlocale(locale.LC_ALL, 'pt_BR.UTF-8')

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
# Leitura das Contas e Perfis
# =============================================================================
@st.cache_data(ttl=3600)  # cache por 1 hora
def load_contas():
    try:
        path = "posicoes/Contas.xlsx"  # ajuste se o caminho for diferente
        df = pd.read_excel(path, sheet_name=0)
        # Limpa nomes de colunas (remove espaços extras)
        df.columns = df.columns.str.strip()
        return df
    except Exception as e:
        st.error(f"Erro ao carregar Contas.xlsx: {e}")
        return pd.DataFrame()

df_contas = load_contas()

try:
    ptax, ptax_data = get_ptax_usdbrl_last()
    st.caption(f"💱 PTAX usada: **{ptax:.4f}** (atualizada em {ptax_data})")
except Exception as e:
    ptax = 5.60
    st.warning(f"Não foi possível obter PTAX automática. Usando valor fixo R$ {ptax:.2f}")

# =============================================================================
# Regras macro
# =============================================================================
RF_BR_BUCKETS = [
    # RF Pós
    ("RF Pós", "Fundos de Invest."),
    ("RF Pós", "Imediato"),
    ("RF Pós", "1 a 30 dias"),
    ("RF Pós", "31 a 180 dias"),
    ("RF Pós", "181 a 360 dias"),
    ("RF Pós", "361+ dias"),
    ("RF Pós", "FiInfra e Cetipados"),
    
    # RF Pré
    ("RF Pré", "Bancário Pré"),
    ("RF Pré", "Tesouro Pré"),
    
    # RF Inflação
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
# FUNDOS RECOMENDADOS - Classificação por categoria e liquidez
# =============================================================================
FUNDOS_RECOMENDADOS = {
    "RF Pós - Liquidez (curto/médio prazo)": [
        {"fundo": "BNP Paribas Match DI",                   "liquidez": "D+0"},
        {"fundo": "Bradesco SKY",                           "liquidez": "D+0"},
        {"fundo": "Safra DI Master",                        "liquidez": "D+0"},
        {"fundo": "Tivio Institucional",                    "liquidez": "D+0"},
        {"fundo": "Tivio Banks",                            "liquidez": "D+0"},
        {"fundo": "BTG Yield DI FIRF Ref CrPr",             "liquidez": "D+0"},
        {"fundo": "Riza Lotus",                             "liquidez": "D+1"},
        {"fundo": "Absolute Atenas",                        "liquidez": "D+1"},
        {"fundo": "Valore FI RF CP",                        "liquidez": "D+5"},
        {"fundo": "Bradesco Zupo",                          "liquidez": "D+6"},
        {"fundo": "BNP Paribas Rubi FIC FIRF CP",           "liquidez": "D+10"},
        {"fundo": "Tivio Institucional 15",                 "liquidez": "D+15"},
        {"fundo": "Absolute Creta",                         "liquidez": "D+31"},
        {"fundo": "Safra Vitesse",                          "liquidez": "D+31"},
        {"fundo": "XP CDI Debêntures Inc",                  "liquidez": "D+31"},
        {"fundo": "Solis Capital Antares Light FIC FIM CP", "liquidez": "D+45"},
        {"fundo": "Solis Capital Pioneiro FIC FIDC",        "liquidez": "D+60"},
        {"fundo": "Jive BossaNova 90 FIDC",                 "liquidez": "D+90"},
        {"fundo": "Kinea Oportunidade",                     "liquidez": "D+90"},
        {"fundo": "TIVIO ALT 90 FIDC RL",                   "liquidez": "D+90"},
        {"fundo": "Tivio Alt Credito High Yield 180 FIDC RL","liquidez": "D+180"},
        {"fundo": "Jive BossaNova High yield Advisory FIC FIM", "liquidez": "D+360"},
    ],
    "RF Inflação - Longo prazo / Incentivados (inclui FI-Infra)": [
        {"fundo": "Capitânia Infra Renda 90 Incentivado Infraestrutura RF CP", "liquidez": "D+90"},
        {"fundo": "JURO11",  "liquidez": "Longo prazo", "indexador": "IPCA"},
        {"fundo": "IFRA11",  "liquidez": "Longo prazo", "indexador": "IPCA"},
        {"fundo": "KDIF11",  "liquidez": "Longo prazo", "indexador": "IPCA"},
        {"fundo": "JGPI11",  "liquidez": "Longo prazo", "indexador": "IPCA"},
        {"fundo": "BDIF11",  "liquidez": "Longo prazo", "indexador": "IPCA"},
        {"fundo": "JMBI11",  "liquidez": "Longo prazo", "indexador": "IPCA"},
        {"fundo": "CPTI11",  "liquidez": "Longo prazo", "indexador": "IPCA"},
    ],
    "RF Pós - FI-Infra / Cetipados (CDI)": [
        {"fundo": "KNDI11",  "liquidez": "Longo prazo", "indexador": "CDI"},
        {"fundo": "CDII11",  "liquidez": "Longo prazo", "indexador": "CDI"},
        {"fundo": "IFRI11",  "liquidez": "Longo prazo", "indexador": "CDI"},
        {"fundo": "AZQI11",  "liquidez": "Longo prazo", "indexador": "CDI"},
        {"fundo": "KNCE11",  "liquidez": "Longo prazo", "indexador": "CDI"},
        {"fundo": "AZIN11",  "liquidez": "Longo prazo", "indexador": "CDI"},
    ],
    "RV Brasil - Fundos de Ações": [
        {"fundo": "SPX Patriot FIC FIA",                "liquidez": "32 dias"},
        {"fundo": "Kaítalo Tarkus FIC FIA",             "liquidez": "32 dias"},
        {"fundo": "Encore Valor Dividendos FIF Ações",  "liquidez": "32 dias"},
        {"fundo": "Absolute Pace LB Advisory FIC FIA",  "liquidez": "32 dias"},
        {"fundo": "Constellation Institucional",        "liquidez": "60 dias"},
        {"fundo": "Encore LB FIC FIM",                  "liquidez": "32 dias"},
        {"fundo": "Dahlia Total Return",                "liquidez": "32 dias"},
        {"fundo": "Real Investor FIC de FIF em Ações",  "liquidez": "30 dias"},
    ]
}

# =============================================================================
# RV baskets (exemplo - ajuste conforme seu código original)
# =============================================================================

# Ações - Carteiras sem foco em renda (Moderada, Arrojada, Conservadora)
ACOES_SEM_RENDA = [
    "AXIA3", "EQTL3", "SBSP3", "ITUB3", "BPAC11", "PSSA3", "PRIO3", "VALE3", "WEGE3", "RENT3"
]

# Ações - Carteiras com geração de renda (Renda Construção, Renda Usufruto, etc.)
ACOES_COM_RENDA = [
    "CPLE3", "EGIE3", "AXIA3", "ITUB3", "VALE3", "ALOS3", "FLRY3", "ABEV3", "PRIO3", "WEGE3"
]

# FIIs (comum para carteiras com renda)
FIIs_RECOMENDADOS = [
    "KNRI11", "XPML11", "HGLG11", "PVBI11", "HGRU11", "KNCR11", "KNIP11", "KNCA11"
]

# Internacional (ETFs globais - mantive sua lista original por enquanto)
RV_INT = ["VOO", "QQQ", "SPY", "VTI", "VXUS"]

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
# TAB 1 - Atualizar posições
# =============================================================================
with tab1:
    st.header("Atualizar posições")
    
    if st.button("Rebuild latest positions", type="primary"):
        with st.spinner("Reconstruindo posição consolidada..."):
            try:
                df = posmod.build_latest_from_repo()
                st.session_state["df_latest"] = df
                st.success("✅ Posição consolidada com sucesso!")
                
                # ===================== MÉTRICAS PRINCIPAIS =====================
                st.subheader("Resumo Patrimonial Consolidado")
                
                # Força a coluna valor_mercado a existir e ser numérica (proteção máxima)
                df["valor_mercado"] = pd.to_numeric(
                    df.get("valor_mercado", pd.Series([0.0] * len(df))), 
                    errors="coerce"
                ).fillna(0.0)
                
                contas_distintas = df[["corretora", "conta"]].drop_duplicates()
                resumo = df.groupby("corretora")["valor_mercado"].agg(["sum"]).reset_index()
                resumo.columns = ["Corretora", "PL"]
                
                contas_por_corretora = df.groupby("corretora")["conta"].nunique().reset_index()
                contas_por_corretora.columns = ["Corretora", "Qtd_Contas"]
                resumo = resumo.merge(contas_por_corretora, on="Corretora", how="left").fillna(0)
                
                # Cálculo dos PLs com proteção total
                pl_wealth = float(resumo["PL"].sum())
                pl_xp = float(resumo.loc[resumo["Corretora"] == "XP", "PL"].sum()) if "XP" in resumo["Corretora"].values else 0.0
                pl_btg = float(resumo.loc[resumo["Corretora"] == "BTG", "PL"].sum()) if "BTG" in resumo["Corretora"].values else 0.0
                pl_cs_brl = float(resumo.loc[resumo["Corretora"] == "CS", "PL"].sum()) if "CS" in resumo["Corretora"].values else 0.0
                pl_cs_usd = pl_cs_brl / ptax if pl_cs_brl > 0 else 0.0
                
                col1, col2, col3, col4 = st.columns(4)
                
                col1.metric("PL Total Wealth", format_brl(pl_wealth), 
                           delta=f"{len(contas_distintas)} contas distintas totais")
                col2.metric("PL XP", format_brl(pl_xp), 
                           delta=f"{int(resumo.loc[resumo['Corretora']=='XP', 'Qtd_Contas'].sum() if not resumo.empty else 0)} contas")
                col3.metric("PL BTG", format_brl(pl_btg), 
                           delta=f"{int(resumo.loc[resumo['Corretora']=='BTG', 'Qtd_Contas'].sum() if not resumo.empty else 0)} contas")
                col4.metric(
                    "PL CS",
                    format_brl(pl_cs_brl),
                    delta=f"US$ {pl_cs_usd:,.2f} • PTAX {ptax:.4f}"
                )
                
                # ===================== EXPANDER COM LISTA COMPLETA =====================
                with st.expander("Ver lista completa de TODOS os ativos consolidados", expanded=False):
                    df_display = df.copy()
                    df_display["valor_mercado"] = pd.to_numeric(
                        df_display["valor_mercado"], errors="coerce"
                    ).fillna(0.0)
                    
                    display_cols = ["corretora", "conta", "asset_id", "asset_nome", "asset_tipo", 
                                   "valor_mercado", "quantidade", "moeda"]          
                    st.dataframe(
                        df_display[display_cols]
                        .sort_values(by=["corretora", "valor_mercado"], ascending=[True, False]),
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "valor_mercado": st.column_config.NumberColumn(
                                "Valor de Mercado",
                                format="R$ %,.2f",
                                help="CS já convertido pela PTAX"
                            ),
                            "quantidade": st.column_config.NumberColumn(
                                "Quantidade",
                                format="%,.4f"
                            )
                        }
                    )
                    st.caption(f"Total de {len(df)} posições consolidadas • {len(contas_distintas)} contas distintas")
                
            except Exception as e:
                st.error(f"Erro ao reconstruir: {e}")
                

# =============================================================================
# TAB 2 - Asset Allocation
# =============================================================================
with tab2:
    st.header("Asset Allocation - Cliente")

    if "df_latest" not in st.session_state:
        st.warning("Faça o Rebuild na aba 'Atualizar posições' primeiro.")
        st.stop()

    df_latest = st.session_state.df_latest.copy()

    # Seleção do Grupo Geral
    grupos = sorted(df_latest["GRUPO GERAL"].dropna().unique())
    grupo_sel = st.selectbox("👥 Grupo Geral (Cliente)", grupos)

    pos_cliente = df_latest[df_latest["GRUPO GERAL"] == grupo_sel].copy()
    pl_total = float(pos_cliente["valor_mercado"].sum())

    # ===================== PERFIL AUTOMÁTICO =====================
    
    perfil_cliente = "Não identificado"
    
    if df_contas is not None and not df_contas.empty and "GRUPO GERAL" in df_contas.columns:
        grupo_sel_str = str(grupo_sel).strip()
        mask = df_contas["GRUPO GERAL"].astype(str).str.strip().eq(grupo_sel_str)
        
        matching = df_contas[mask]
        
        if not matching.empty:
            perfis = matching["Perfil Carteira"].dropna().astype(str).str.strip()
            if not perfis.empty:
                perfil_cliente = perfis.iloc[0]

    st.caption(f" **Perfil detectado na planilha Contas:** {perfil_cliente}")

    # ===================== MAPEAMENTO EXPLÍCITO PERFIL → MODELO =====================
    pesos = load_pesos_xlsx()
    modelos = list(pesos.keys())

    modelo_default = None
    perfil_norm = perfil_cliente.strip().upper()

    if "ARROJADO RENDA CONSTRUÇÃO" in perfil_norm:
        modelo_default = "Arrojado Renda Construção"
    elif "MODERADO RENDA CONSTRUÇÃO" in perfil_norm:
        modelo_default = "Moderado Renda Construção"
    elif "CONSERVADOR RENDA CONSTRUÇÃO" in perfil_norm:
        modelo_default = "Conservador Renda Construção"
    elif "MODERADO RENDA USUFRUTO" in perfil_norm:
        modelo_default = "Moderado Renda Usufruto"
    elif "CONSERVADOR RENDA USUFRUTO" in perfil_norm:
        modelo_default = "Conservador Renda Usufruto"
    elif "ARROJADO RENDA USUFRUTO" in perfil_norm:
        modelo_default = "Arrojado Renda Usufruto"
    elif "ARROJADO" in perfil_norm:
        modelo_default = "Arrojado"
    elif "MODERADO" in perfil_norm:
        modelo_default = "Moderado"
    elif "CONSERVADOR" in perfil_norm:
        modelo_default = "Conservador"
    elif "ULTRACONSERVADOR" in perfil_norm:
        modelo_default = "Ultraconservador"

    if modelo_default and modelo_default in modelos:
        default_idx = modelos.index(modelo_default)
    else:
        default_idx = 0
        st.warning(f" Perfil '{perfil_cliente}' não mapeado. Usando '{modelos[0]}'.")

    modelo = st.selectbox(
        "Modelo de alocação (padrão = perfil do cliente)",
        modelos,
        index=default_idx,
        help="Mapeado automaticamente da planilha Contas.xlsx."
    )

    p = pesos[modelo]

    # PTAX
    try:
        ptax, _ = get_ptax_usdbrl_last()
    except:
        ptax = 5.60

    # ===================== DETALHAMENTO POR CORRETORA (SÓ MÉTRICAS) =====================
    st.subheader("Detalhamento por corretora")

    por_corretora = pos_cliente.groupby("corretora", as_index=False)["valor_mercado"].agg(
        PL_total="sum", Qtd_ativos="count"
    ).sort_values("PL_total", ascending=False)

    por_corretora["PL_fmt"] = por_corretora["PL_total"].apply(format_brl)
    por_corretora["% do total"] = (por_corretora["PL_total"] / pl_total * 100).round(1).astype(str) + "%"

    cols = st.columns(len(por_corretora) + 1)
    cols[0].metric("PL Global", format_brl(pl_total))
    for i, row in por_corretora.iterrows():
        cols[i+1].metric(row["corretora"], row["PL_fmt"], delta=f"{row['% do total']} • {row['Qtd_ativos']} ativos")

    with st.expander("Ver detalhamento por conta individual"):
        por_conta = pos_cliente.groupby(["corretora", "conta"], as_index=False).agg(
            PL=("valor_mercado", "sum"),
            Ativos=("asset_id", "nunique"),
            Cliente=("CLIENTE", "first")
        ).sort_values("PL", ascending=False)

        por_conta["PL_fmt"] = por_conta["PL"].apply(format_brl)
        por_conta["% do grupo"] = (por_conta["PL"] / pl_total * 100).round(1).astype(str) + "%"

        st.dataframe(
            por_conta[["corretora", "conta", "Cliente", "PL_fmt", "% do grupo", "Ativos"]],
            hide_index=True,
            use_container_width=True
        )

    rf_br_w, rv_br_w, intl_w, _, _ = macro_weights_from_neutro(p)

    alvo_rf  = pl_total * rf_br_w
    alvo_rv  = pl_total * rv_br_w
    alvo_int = pl_total * intl_w

    # ===================== 1) Comparativo MACRO =====================
    st.subheader("1) Comparativo Macro")

    def classifica_macro(row):
        if row["corretora"] == "CS":
            return "Internacional"
        
        # ✅ TRATA NaN corretamente
        asset_tipo = str(row.get("asset_tipo", "") or "").strip()
        mercado = str(row.get("mercado", "") or "").strip()
        at = (asset_tipo + " " + mercado).upper()
        
        if any(x in at for x in ["ACAO","FII","EQUITY","ETF","RV","AÇÕES"]):
            return "RV Brasil"
        return "RF Brasil"


    pos_cliente["macro"] = pos_cliente.apply(classifica_macro, axis=1)
    atual_macro = pos_cliente.groupby("macro")["valor_mercado"].sum().reindex(["RF Brasil","RV Brasil","Internacional"]).fillna(0)

    macro_df = pd.DataFrame({
        "Categoria": ["RF Brasil", "RV Brasil", "Internacional"],
        "Atual": [format_brl(atual_macro.get(c,0)) for c in ["RF Brasil","RV Brasil","Internacional"]],
        "Alvo":  [format_brl(v) for v in [alvo_rf, alvo_rv, alvo_int]],
        "Diferença": [format_brl(atual_macro.get(c,0) - v) for c,v in zip(["RF Brasil","RV Brasil","Internacional"], [alvo_rf,alvo_rv,alvo_int])]
    })

    st.dataframe(
        macro_df.style.applymap(style_compra_venda, subset=["Diferença"]),
        use_container_width=True,
        hide_index=True
    )

    # ===================== 2) RF BRASIL - SUB-BUCKETS =====================
    
    with st.expander("2) RF Brasil - Detalhamento Completo por Sub-Bucket", expanded=True):
        pos_rf = pos_cliente[pos_cliente["macro"] == "RF Brasil"].copy()
        
        if pos_rf.empty:
            st.info("Nenhuma posição em RF Brasil.")
        else:
            def sub_bucket_rf_detalhado(row):
                estr = (str(row.get("estrategia","")) + " " + 
                        str(row.get("sub_mercado","")) + " " + 
                        str(row.get("mercado","")) + " " + 
                        str(row.get("asset_tipo",""))).upper()
                
                # RF Pós
                if any(x in estr for x in ["IMEDIATO","LIQUIDEZ","D+0","D+1"]): return "Imediato"
                if any(x in estr for x in ["1 A 30","CURTO PRAZO"]): return "1 a 30 dias"
                if any(x in estr for x in ["31 A 180"]): return "31 a 180 dias"
                if any(x in estr for x in ["181 A 360"]): return "181 a 360 dias"
                if any(x in estr for x in ["361+","LONGO PRAZO"]): return "361+ dias"
                if "FIINFRA" in estr or "CETIPADO" in estr: return "FiInfra e Cetipados"
                
                # RF Pré
                if any(x in estr for x in ["BANCARIO PRE","BANCO PRE"]): return "Bancário Pré"
                if any(x in estr for x in ["TESOURO PRE","NTN-F","LTN"]): return "Tesouro Pré"
                
                # RF Inflação
                if any(x in estr for x in ["BANCARIO","BANCO"]): return "Bancário"
                if any(x in estr for x in ["TESOURO","NTN-B","NTNB"]): return "Tesouro"
                if "FIINFRA" in estr or "CETIPADO" in estr: return "FiInfra e Cetipado"
                if any(x in estr for x in ["CREDITO PRIVADO","CRI","CRA","DEBENTURE"]): return "Crédito Privado"
                
                return "Outros"
            
            
            # Dentro do expander de RF Brasil (ou em um novo expander específico)
        with st.expander("RF Brasil - Fundos Recomendados por Categoria", expanded=False):
            # Liquidez curto prazo
            st.subheader("Liquidez (RF Pós - curto/médio prazo)")
            df_liq = pd.DataFrame(FUNDOS_RECOMENDADOS["RF Pós - Liquidez (curto/médio prazo)"])
            st.dataframe(df_liq[["fundo", "liquidez"]], hide_index=True, use_container_width=True)
        
            # RF Inflação + FI-Infra
            st.subheader("RF Inflação / Longo Prazo (inclui FI-Infra incentivados)")
            df_infl = pd.DataFrame(FUNDOS_RECOMENDADOS["RF Inflação - Longo prazo / Incentivados (inclui FI-Infra)"])
            st.dataframe(df_infl[["fundo", "liquidez", "indexador"]], hide_index=True, use_container_width=True)
        
            # RF Pós FI-Infra CDI
            st.subheader("RF Pós - FI-Infra / Cetipados (indexador CDI)")
            df_cdi = pd.DataFrame(FUNDOS_RECOMENDADOS["RF Pós - FI-Infra / Cetipados (CDI)"])
            st.dataframe(df_cdi[["fundo", "liquidez", "indexador"]], hide_index=True, use_container_width=True)
        
            # Fundos de Ações (RV Brasil)
            st.subheader("RV Brasil - Fundos de Ações recomendados")
            df_rv_fundos = pd.DataFrame(FUNDOS_RECOMENDADOS["RV Brasil - Fundos de Ações"])
            st.dataframe(df_rv_fundos[["fundo", "liquidez"]], hide_index=True, use_container_width=True)
    
            pos_rf["sub_bucket"] = pos_rf.apply(sub_bucket_rf_detalhado, axis=1)
            atual_rf = pos_rf.groupby("sub_bucket")["valor_mercado"].sum()
    
            # === Alvos vindos DIRETO da planilha (por modelo) ===
            sub_keys = [
                "Imediato","1 a 30 dias","31 a 180 dias","181 a 360 dias","361+ dias",
                "FiInfra e Cetipados","Bancário Pré","Tesouro Pré",
                "Bancário","Tesouro","FiInfra e Cetipado","Crédito Privado"
            ]
            
            alvo_sub = {k: pl_total * float(p.get(k, 0.0)) for k in sub_keys}
    
            rf_detail = pd.DataFrame({
                "Sub-Bucket": list(atual_rf.index),
                "Atual (R$)":    [format_brl(v) for v in atual_rf.values],
                "Alvo (R$)":     [format_brl(alvo_sub.get(k, 0)) for k in atual_rf.index],
                "Diferença (R$)":[format_brl(v - alvo_sub.get(k, 0)) for k, v in atual_rf.items()]
            })
    
            # Ordenação correta
            ordem = {name: i for i, name in enumerate(sub_keys)}
            rf_detail = rf_detail.assign(ordem=rf_detail["Sub-Bucket"].map(ordem)).sort_values("ordem").drop(columns="ordem")
    
            st.dataframe(
                rf_detail.style.applymap(style_compra_venda, subset=["Diferença (R$)"]),
                use_container_width=True,
                hide_index=True
            )
            
    # ===================== 3) RV BRASIL - ATUAL + SUGERIDO =====================
    with st.expander("3) RV Brasil - Atual vs Sugerido", expanded=True):
        rv_real = pos_cliente[pos_cliente["macro"] == "RV Brasil"].copy()
    
        # Escolhe a lista de ações correta com base no modelo/perfil
        if "Renda" in modelo.upper():
            acoes_rec = [
                "CPLE3", "EGIE3", "AXIA3", "ITUB3", "VALE3", "ALOS3", "FLRY3", "ABEV3", "PRIO3", "WEGE3"
            ]
        else:
            acoes_rec = [
                "AXIA3", "EQTL3", "SBSP3", "ITUB3", "BPAC11", "PSSA3", "PRIO3", "VALE3", "WEGE3", "RENT3"
            ]
    
        fiis_rec = [
            "KNRI11", "XPML11", "HGLG11", "PVBI11", "HGRU11", "KNCR11", "KNIP11", "KNCA11"
        ]
    
        # Todos os ativos recomendados para RV Brasil
        tickers_rv = acoes_rec + fiis_rec
    
        if not tickers_rv:
            st.warning("Nenhum ticker definido para RV Brasil neste perfil.")
            st.stop()
    
        # Peso igual por ativo (você pode ajustar para pesos diferentes depois)
        peso_por_ativo = alvo_rv / len(tickers_rv)
    
        if rv_real.empty:
            st.info("Cliente sem posições em RV Brasil no momento.")
            sugestao = []
            for t in tickers_rv:
                sugestao.append([t, "R$ 0,00", format_brl(peso_por_ativo), format_brl(peso_por_ativo)])
            
            rv_df = pd.DataFrame(sugestao, columns=["Ativo", "Atual (R$)", "Sugerido (R$)", "Diferença (R$)"])
        else:
            # Agrupar posições reais por ticker
            rv_real_group = rv_real.groupby("asset_id").agg({
                "valor_mercado": "sum",
                "asset_nome": "first"  # nome amigável se existir
            }).reset_index()
    
            sugestao = []
            for t in tickers_rv:
                atual = rv_real_group[rv_real_group["asset_id"] == t]["valor_mercado"].sum() if t in rv_real_group["asset_id"].values else 0.0
                diff = peso_por_ativo - atual  # Invertido: positivo = comprar (verde), negativo = vender (vermelho)
                sugestao.append([
                    t,
                    format_brl(atual),
                    format_brl(peso_por_ativo),
                    format_brl(diff)
                ])
    
            rv_df = pd.DataFrame(sugestao, columns=["Ativo", "Atual (R$)", "Sugerido (R$)", "Diferença (R$)"])
    
        # Função de estilo (verde = comprar/faltando, vermelho = vender/excesso)
        def style_diff_rv(val):
            try:
                num_str = str(val).replace("R$", "").replace(".", "").replace(",", ".").strip()
                num = float(num_str)
                if num > 0:   # positivo → precisa comprar → verde
                    return "color: #2e7d32; font-weight: 650;"
                if num < 0:   # negativo → excesso → vermelho
                    return "color: #c62828; font-weight: 650;"
            except:
                pass
            return "color: #757575;"
    
        st.dataframe(
            rv_df.style.applymap(style_diff_rv, subset=["Diferença (R$)"]),
            use_container_width=True,
            hide_index=True
        )
    
        # Legenda clara
        st.caption("🟢 Positivo = precisa comprar   🔴 Negativo = excesso (pode vender/reduzir)")
        
    # ===================== 4) INTERNACIONAL (RF global + RV separada) =====================
    
    with st.expander("4) Internacional", expanded=True):
        intl_real = pos_cliente[pos_cliente["macro"] == "Internacional"].copy()
        total_intl_atual_brl = intl_real["valor_mercado"].sum()
        total_intl_atual_usd = total_intl_atual_brl / ptax

        st.metric("Total Internacional Atual", f"US$ {total_intl_atual_usd:,.2f} (R$ {format_brl(total_intl_atual_brl)})")

        # RF Internacional (global)
        intl_rf_atual = intl_real[intl_real["asset_tipo"].str.contains("Fixed|Bond|Treasury|Debenture", case=False, na=False)]["valor_mercado"].sum()
        diff_rf_intl = intl_rf_atual - (alvo_int * 0.5)  # assumindo 50% RF / 50% RV - ajuste se quiser

        st.write(f"**RF Internacional** → Atual: {format_brl(intl_rf_atual)} | Alvo aproximado: {format_brl(alvo_int*0.5)} | Diferença: {format_brl(diff_rf_intl)}")

        # RV Internacional
        st.subheader("RV Internacional - Atual vs Sugerido")
        rv_int_real = intl_real[~intl_real["asset_tipo"].str.contains("Fixed|Bond|Treasury", case=False, na=False)].copy()
        rv_int_group = rv_int_real.groupby("asset_id").agg({"valor_mercado":"sum","asset_nome":"first"}).reset_index()

        sugestao_int = {t: (alvo_int * 0.5) / ptax * (1/len(RV_INT)) for t in RV_INT}  # metade do alvo em USD

        int_table = []
        for t, val_usd in sugestao_int.items():
            atual_usd = rv_int_group[rv_int_group["asset_id"] == t]["valor_mercado"].sum() / ptax if t in rv_int_group["asset_id"].values else 0
            diff_usd = atual_usd - val_usd
            int_table.append([t, format_usd(atual_usd), format_usd(val_usd), format_usd(diff_usd), format_brl(atual_usd*ptax), format_brl(val_usd*ptax)])

        int_df = pd.DataFrame(int_table, columns=["Ativo","Atual US$","Sugerido US$","Dif US$","Atual R$","Sugerido R$"])
        st.dataframe(
            int_df.style.applymap(style_compra_venda, subset=["Dif US$"]),
            use_container_width=True,
            hide_index=True
        )

# ===================== TAB CARTEIRA TEÓRICA (com sub-buckets) =====================

with tab3:
    st.header("Carteira Teórica - Detalhada")
    pesos = load_pesos_xlsx()
    modelo = st.selectbox("Modelo", list(pesos.keys()))
    valor = st.number_input("Patrimônio simulado R$", value=1_000_000, step=100_000)

    p = pesos[modelo]

    # Tabela completa com sub-buckets
    linhas = []
    for macro in ["RF Pós", "RF Pré", "RF Inflação", "RV Brasil", "Internacional"]:
        peso_macro = sum(float(p.get(k, 0)) for k in p if macro in k)
        linhas.append([macro, f"{peso_macro:.1%}", format_brl(valor * peso_macro), ""])
        
        # Sub-buckets do macro
        for sub in [k for k in p.keys() if macro in k and k != macro]:
            w = float(p.get(sub, 0))
            if w > 0:
                linhas.append([f"   └ {sub}", f"{w:.1%}", format_brl(valor * w), ""])

    teor_df = pd.DataFrame(linhas, columns=["Estratégia / Sub-Bucket", "Peso", "Valor Alvo (R$)", ""])
    st.dataframe(teor_df, use_container_width=True, hide_index=True)