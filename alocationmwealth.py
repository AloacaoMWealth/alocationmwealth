import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
from datetime import datetime, timedelta
import requests
import json
from pathlib import Path
import positions as posmod
import plotly.express as px

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
@st.cache_data(ttl=3600)
def load_contas():
    try:
        path = "posicoes/Contas.xlsx"
        df = pd.read_excel(path, sheet_name=0)
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
def macro_weights_from_neutro(p):
    rv_br = float(p.get("RV Brasil", 0.0))
    intl = float(p.get("Internacional", p.get("Internacional ", 0.0)))
    rf_br = max(0.0, 1.0 - rv_br - intl)
    return rf_br, rv_br, intl

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
                
                with st.expander("Ver lista completa de TODOS os ativos consolidados", expanded=False):
                    df_display = df.copy()
                    df_display["valor_mercado"] = pd.to_numeric(df_display["valor_mercado"], errors="coerce").fillna(0.0)
                    df_display["quantidade"] = pd.to_numeric(df_display["quantidade"], errors="coerce").fillna(0.0)
                    
                    display_cols = ["corretora", "conta", "asset_id", "asset_nome", "asset_tipo", 
                                    "valor_mercado", "quantidade", "moeda"]
                    
                    def fmt_valor(x):
                        return f"R$ {x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                    
                    def fmt_qtd(x):
                        return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                    
                    styled_df = df_display[display_cols].sort_values(
                        by=["corretora", "valor_mercado"], ascending=[True, False]
                    ).style.format({
                        "valor_mercado": fmt_valor,
                        "quantidade": fmt_qtd
                    })
                    
                    st.dataframe(
                        styled_df,
                        use_container_width=True,
                        hide_index=True
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

    # ===================== SELEÇÃO DE GRUPO E CONTA =====================
    col_g, col_c, col_m = st.columns([3, 3, 2])

    with col_g:
        grupos = sorted(df_latest["GRUPO GERAL"].dropna().unique())
        grupo_sel = st.selectbox("👥 Grupo Geral (Cliente)", grupos)

    with col_c:
        # Opções: "Todas as contas" + contas individuais com nome do cliente
        contas_info = df_latest[df_latest["GRUPO GERAL"] == grupo_sel][["conta", "CLIENTE"]].drop_duplicates()
        opcoes = ["Todas as contas"]
        for _, row in contas_info.iterrows():
            nome = str(row["CLIENTE"]).strip() if pd.notna(row["CLIENTE"]) else ""
            exib = f"{nome} ({row['conta']})" if nome else row["conta"]
            opcoes.append(exib)

        selecao = st.selectbox("Conta", opcoes)

        if selecao == "Todas as contas":
            pos_cliente = df_latest[df_latest["GRUPO GERAL"] == grupo_sel].copy()
        else:
            # Extrai o número da conta da string exibida
            conta_real = selecao.split("(")[-1].strip(")")
            pos_cliente = df_latest[(df_latest["GRUPO GERAL"] == grupo_sel) & 
                                   (df_latest["conta"] == conta_real)].copy()

    with col_m:
        pesos = load_pesos_xlsx()
        perfil_cliente = "Não identificado"
        if not df_contas.empty:
            matching = df_contas[df_contas["GRUPO GERAL"].astype(str).str.strip() == str(grupo_sel).strip()]
            if not matching.empty:
                perfil_cliente = matching["Perfil Carteira"].iloc[0]

        # Mapeamento de modelo
        modelo_default = None
        perfil_norm = perfil_cliente.upper()
        if "ARROJADO RENDA CONSTRUÇÃO" in perfil_norm: modelo_default = "Arrojado Renda Construção"
        elif "MODERADO RENDA CONSTRUÇÃO" in perfil_norm: modelo_default = "Moderado Renda Construção"
        elif "CONSERVADOR RENDA CONSTRUÇÃO" in perfil_norm: modelo_default = "Conservador Renda Construção"
        elif "MODERADO RENDA USUFRUTO" in perfil_norm: modelo_default = "Moderado Renda Usufruto"
        elif "CONSERVADOR RENDA USUFRUTO" in perfil_norm: modelo_default = "Conservador Renda Usufruto"
        elif "ARROJADO RENDA USUFRUTO" in perfil_norm: modelo_default = "Arrojado Renda Usufruto"
        elif "ARROJADO" in perfil_norm: modelo_default = "Arrojado"
        elif "MODERADO" in perfil_norm: modelo_default = "Moderado"
        elif "CONSERVADOR" in perfil_norm: modelo_default = "Conservador"
        elif "ULTRACONSERVADOR" in perfil_norm: modelo_default = "Ultraconservador"

        default_idx = list(pesos.keys()).index(modelo_default) if modelo_default in pesos else 0
        modelo = st.selectbox("Modelo de alocação", list(pesos.keys()), index=default_idx)

    p = pesos[modelo]
    pl_total = float(pos_cliente["valor_mercado"].sum())

    st.caption(f"**Perfil:** {perfil_cliente} | PL atual: **{format_brl(pl_total)}**")

    rf_br_w, rv_br_w, intl_w, _, _ = macro_weights_from_neutro(p)
    alvo_rf = pl_total * rf_br_w
    alvo_rv = pl_total * rv_br_w
    alvo_int = pl_total * intl_w

    # ===================== 1) VISÃO MACRO - DOIS GRÁFICOS DIFERENTES =====================
    st.subheader("Visão Macro Geral")

    def classifica_macro(row):
        if row.get("corretora") == "CS":
            return "Internacional"
        at = (str(row.get("asset_tipo","")) + " " + str(row.get("mercado",""))).upper()
        return "RV Brasil" if any(x in at for x in ["ACAO","FII","EQUITY","ETF","RV","AÇÃO"]) else "RF Brasil"

    pos_cliente["macro"] = pos_cliente.apply(classifica_macro, axis=1)
    atual_macro = pos_cliente.groupby("macro")["valor_mercado"].sum()

    macro_df = pd.DataFrame({
        "Categoria": ["RF Brasil", "RV Brasil", "Internacional"],
        "Atual": [atual_macro.get(c, 0) for c in ["RF Brasil", "RV Brasil", "Internacional"]],
        "Alvo": [alvo_rf, alvo_rv, alvo_int],
        "Diferença": [alvo - atual_macro.get(c, 0) for c, alvo in zip(["RF Brasil","RV Brasil","Internacional"], [alvo_rf, alvo_rv, alvo_int])]
    })

    col_left, col_right = st.columns(2)

    with col_left:
        fig_atual = px.pie(macro_df, names="Categoria", values="Atual", title="Carteira Atual do Cliente")
        fig_atual.update_layout(height=380)
        st.plotly_chart(fig_atual, use_container_width=True)

    with col_right:
        # Gráfico da alocação ideal
        ideal_df = pd.DataFrame({
            "Categoria": ["RF Brasil", "RV Brasil", "Internacional"],
            "Valor Ideal": [alvo_rf, alvo_rv, alvo_int]
        })
        fig_ideal = px.pie(ideal_df, names="Categoria", values="Valor Ideal", title="Alocação Ideal (Modelo)")
        fig_ideal.update_layout(height=380)
        st.plotly_chart(fig_ideal, use_container_width=True)

    st.dataframe(
        macro_df.style.format({"Atual": format_brl, "Alvo": format_brl, "Diferença": format_brl})
                     .map(style_compra_venda, subset=["Diferença"]),
        use_container_width=True, hide_index=True
    )

    # ===================== 2) RENDA FIXA BRASIL (ÚNICO EXPANDER) =====================
    with st.expander("Renda Fixa Brasil", expanded=True):
        st.subheader("Distribuição por Sub-Estratégia")

        pos_rf = pos_cliente[pos_cliente["macro"] == "RF Brasil"].copy()

        def sub_bucket_rf_detalhado(row):
            asset_id = str(row.get("asset_id", "")).upper()
            nome = str(row.get("asset_nome", "")).upper()
            tipo = str(row.get("asset_tipo", "")).upper()
            estr = f"{asset_id} {nome} {tipo}"

            # FI-Infra
            if any(code in asset_id for code in ["KNDI11","CDII11","IFRI11","AZQI11","KNCE11","AZIN11","JURO11","IFRA11","KDIF11","JGPI11","BDIF11","JMBI11","CPTI11"]):
                return "FiInfra e Cetipados"

            # Fundos de Investimento
            if any(x in estr for x in ["FIC","FIA","FIDC","FIM","DI","SKY","MATCH","TIVIO","ABSOLUTE","SAFRA","BNP","BRADESCO","XP CDI","SOLIS","JIVE","KINEA"]):
                return "Fundos de Investimento"

            if any(x in estr for x in ["IMEDIATO","LIQUIDEZ","D+0","D+1"]): return "Imediato"
            if any(x in estr for x in ["1 A 30","CURTO"]): return "1 a 30 dias"
            if any(x in estr for x in ["31 A 180"]): return "31 a 180 dias"
            if any(x in estr for x in ["181 A 360"]): return "181 a 360 dias"
            if any(x in estr for x in ["361+","LONGO"]): return "361+ dias"

            if any(x in estr for x in ["BANCARIO PRE","BANCO PRE"]): return "Bancário Pré"
            if any(x in estr for x in ["TESOURO PRE","NTN-F","LTN"]): return "Tesouro Pré"

            if any(x in estr for x in ["BANCARIO","BANCO"]): return "Bancário"
            if any(x in estr for x in ["TESOURO","NTN-B","NTNB"]): return "Tesouro"
            if any(x in estr for x in ["CREDITO PRIVADO","CRI","CRA","DEBENTURE"]): return "Crédito Privado"

            return "Outros"

        pos_rf["sub_bucket"] = pos_rf.apply(sub_bucket_rf_detalhado, axis=1)
        atual_rf = pos_rf.groupby("sub_bucket")["valor_mercado"].sum()

        sub_keys = ["Fundos de Investimento", "Imediato", "1 a 30 dias", "31 a 180 dias", "181 a 360 dias", "361+ dias",
                    "FiInfra e Cetipados", "Bancário Pré", "Tesouro Pré", "Bancário", "Tesouro", "Crédito Privado"]
        alvo_sub = {k: pl_total * float(p.get(k, 0.0)) for k in sub_keys}

        rf_detail = pd.DataFrame({
            "Sub-Bucket": list(atual_rf.index),
            "Atual (R$)": [format_brl(v) for v in atual_rf.values],
            "Alvo (R$)": [format_brl(alvo_sub.get(k, 0)) for k in atual_rf.index],
            "Diferença (R$)": [format_brl(alvo_sub.get(k, 0) - v) for k, v in atual_rf.items()]
        })

        st.dataframe(
            rf_detail.style.map(style_compra_venda, subset=["Diferença (R$)"]),
            use_container_width=True, hide_index=True
        )

        with st.expander("FI-Infra e Cetipados - Posição Atual vs Alvo"):
            fi_infra = pos_rf[pos_rf["sub_bucket"].astype(str).str.contains("FiInfra", na=False)]
            if not fi_infra.empty:
                st.dataframe(
                    fi_infra[["asset_id", "asset_nome", "valor_mercado", "quantidade"]]
                    .style.format({"valor_mercado": format_brl}),
                    hide_index=True, use_container_width=True
                )
            else:
                st.info("Nenhuma posição em FI-Infra encontrada.")

    # ===================== 3) RV BRASIL =====================
    with st.expander("3) Renda Variável Brasil", expanded=False):
        rv_real = pos_cliente[pos_cliente["macro"] == "RV Brasil"].copy()

        acoes_sem_renda = ["AXIA3", "EQTL3", "SBSP3", "ITUB3", "BPAC11", "PSSA3", "PRIO3", "VALE3", "WEGE3", "RENT3"]
        acoes_com_renda = ["CPLE3", "EGIE3", "AXIA3", "ITUB3", "VALE3", "ALOS3", "FLRY3", "ABEV3", "PRIO3", "WEGE3"]
        fiis_recomendados = ["KNRI11", "XPML11", "HGLG11", "PVBI11", "HGRU11", "KNCR11", "KNIP11", "KNCA11"]

        if "RENDA" in modelo.upper():
            recomendados = acoes_com_renda + fiis_recomendados
        else:
            recomendados = acoes_sem_renda + fiis_recomendados

        dentro = rv_real[rv_real["asset_id"].isin(recomendados)].copy()
        fora = rv_real[~rv_real["asset_id"].isin(recomendados)].copy()

        st.metric("Alvo RV Brasil", format_brl(alvo_rv))

        col1, col2 = st.columns(2)
        col1.metric("Dentro da estratégia", format_brl(dentro["valor_mercado"].sum() if not dentro.empty else 0))
        col2.metric("Fora da estratégia", format_brl(fora["valor_mercado"].sum() if not fora.empty else 0))

        if not fora.empty:
            with st.expander("Ver ativos fora da estratégia"):
                st.dataframe(
                    fora[["asset_id", "asset_nome", "valor_mercado", "quantidade"]]
                    .style.format({"valor_mercado": format_brl}),
                    hide_index=True, 
                    use_container_width=True
                )

        # Sugestão de alocação
        sugestao = []
        peso_por_ativo = alvo_rv / len(recomendados) if recomendados else 0
        for t in recomendados:
            atual_val = dentro[dentro["asset_id"] == t]["valor_mercado"].sum() if not dentro.empty else 0
            atual_qtd = dentro[dentro["asset_id"] == t]["quantidade"].sum() if not dentro.empty else 0
            diff_val = peso_por_ativo - atual_val
            sugestao.append([t, format_brl(atual_val), atual_qtd, format_brl(peso_por_ativo), format_brl(diff_val)])

        rv_df = pd.DataFrame(sugestao, columns=["Ativo", "Atual (R$)", "Qtd Atual", "Sugerido (R$)", "Diferença (R$)"])
        
        st.dataframe(
            rv_df.style.map(style_compra_venda, subset=["Diferença (R$)"]),
            use_container_width=True, 
            hide_index=True
        )

    # ===================== 4) INTERNACIONAL =====================
    with st.expander("Internacional", expanded=True):
        intl_real = pos_cliente[pos_cliente["macro"] == "Internacional"].copy()
        rf_int = intl_real[intl_real["asset_tipo"].str.contains("Fixed|Bond|Treasury|Debenture", case=False, na=False)]
        rv_int = intl_real[~intl_real["asset_tipo"].str.contains("Fixed|Bond|Treasury|Debenture", case=False, na=False)]

        total_usd = intl_real["valor_mercado"].sum() / ptax if ptax > 0 else 0

        st.metric("Total Internacional", f"US$ {total_usd:,.2f} (R$ {format_brl(total_usd*ptax)})")

        col1, col2 = st.columns(2)
        col1.metric("RF Internacional", f"US$ {(rf_int['valor_mercado'].sum()/ptax):,.2f}" if ptax > 0 else "US$ 0.00")
        col2.metric("RV Internacional", f"US$ {(rv_int['valor_mercado'].sum()/ptax):,.2f}" if ptax > 0 else "US$ 0.00")

        with st.expander("Lista RF Internacional"):
            st.dataframe(rf_int[["asset_id","asset_nome","valor_mercado","quantidade"]].style.format({"valor_mercado": format_brl}), hide_index=True, use_container_width=True)
        with st.expander("Lista RV Internacional"):
            st.dataframe(rv_int[["asset_id","asset_nome","valor_mercado","quantidade"]].style.format({"valor_mercado": format_brl}), hide_index=True, use_container_width=True)


# =============================================================================
# TAB 3 - Carteira Teórica
# =============================================================================
with tab3:
    st.header("Carteira Teórica - Detalhada")
    pesos = load_pesos_xlsx()
    modelo = st.selectbox("Modelo", list(pesos.keys()))
    valor = st.number_input("Patrimônio simulado R$", value=1_000_000, step=100_000)

    p = pesos[modelo]

    linhas = []
    for macro in ["RF Pós", "RF Pré", "RF Inflação", "RV Brasil", "Internacional"]:
        peso_macro = sum(float(p.get(k, 0)) for k in p if macro in k)
        linhas.append([macro, f"{peso_macro:.1%}", format_brl(valor * peso_macro), ""])
        
        for sub in [k for k in p.keys() if macro in k and k != macro]:
            w = float(p.get(sub, 0))
            if w > 0:
                linhas.append([f"   └ {sub}", f"{w:.1%}", format_brl(valor * w), ""])

    teor_df = pd.DataFrame(linhas, columns=["Estratégia / Sub-Bucket", "Peso", "Valor Alvo (R$)", ""])
    st.dataframe(teor_df, use_container_width=True, hide_index=True)

st.caption("M Wealth Asset Allocation")