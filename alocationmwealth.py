import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
from datetime import datetime, timedelta
import requests
import json
from pathlib import Path

import positions as posmod  # seu módulo positions.py

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

# =============================================================================
# Regras macro
# =============================================================================
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

# =============================================================================
# RV baskets (exemplo - ajuste conforme seu código original)
# =============================================================================
RV_BR_ACOES = ["ITUB4", "VALE3", "PETR4", "B3SA3", "WEGE3", "ABEV3", "BBAS3", "MGLU3"]
RV_BR_FIIS  = ["KNRI11", "HGLG11", "XPML11", "MXRF11", "VISC11", "HGRE11"]
RV_INT      = ["VOO", "QQQ", "SPY", "VTI", "VXUS"]

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
    if st.button("Rebuild latest positions"):
        with st.spinner("Reconstruindo posição consolidada..."):
            try:
                df = posmod.build_latest_from_repo()
                st.session_state["df_latest"] = df
                st.success("Posição consolidada com sucesso!")
                st.dataframe(df.head(10))
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
    grupo_sel = st.selectbox("Grupo Geral (Cliente)", grupos)

    pos_cliente = df_latest[df_latest["GRUPO GERAL"] == grupo_sel].copy()
    pl_total = float(pos_cliente["valor_mercado"].sum())

    # ===================== PERFIL AUTOMÁTICO =====================
    perfil_cliente = "Não identificado"
    if not df_contas.empty:
        matching = df_contas[
            df_contas["GRUPO GERAL"].astype(str).str.strip() == str(grupo_sel).strip()
        ]
        if not matching.empty:
            perfis = matching["Perfil Carteira"].dropna().astype(str).str.strip()
            if not perfis.empty:
                perfil_cliente = perfis.iloc[0]

    st.caption(f"**Perfil detectado na planilha Contas:** {perfil_cliente}")

    # ===================== ÚNICO SELECTBOX DE MODELO (MATCHING FORTE) =====================
    pesos = load_pesos_xlsx()
    modelos = list(pesos.keys())

    # MATCHING MELHORADO - agora reconhece exatamente "Arrojado Renda Construção", "Moderado Renda Construção", etc.
    default_idx = 0
    perfil_norm = perfil_cliente.strip()

    for i, m in enumerate(modelos):
        if perfil_norm == m or perfil_norm.upper() == m.upper():
            default_idx = i
            break
        # Fallback para casos com pequenas diferenças
        if ("RENDA CONSTRUÇÃO" in perfil_norm.upper() and "RENDA CONSTRUÇÃO" in m.upper()) or \
           ("RENDA USUFRUTO" in perfil_norm.upper() and "RENDA USUFRUTO" in m.upper()):
            default_idx = i
            break

    modelo = st.selectbox(
        "🎯 Modelo de alocação (padrão = perfil do cliente)",
        modelos,
        index=default_idx,
        help="Puxado automaticamente da planilha Contas.xlsx. Altere apenas para simular outro cenário."
    )

    p = pesos[modelo]   # ← Este é o modelo que realmente controla todos os cálculos

    # PTAX
    try:
        ptax, _ = get_ptax_usdbrl_last()
    except:
        ptax = 5.60

    # ===================== DETALHAMENTO POR CORRETORA (ÚNICO) =====================
    st.subheader("Detalhamento por corretora")

    por_corretora = pos_cliente.groupby("corretora", as_index=False)["valor_mercado"].agg(
        PL_total="sum", Qtd_ativos="count"
    ).sort_values("PL_total", ascending=False)

    por_corretora["PL_fmt"] = por_corretora["PL_total"].apply(format_brl)
    por_corretora["% do total"] = (por_corretora["PL_total"] / pl_total * 100).round(1).astype(str) + "%"

    st.dataframe(
        por_corretora[["corretora", "PL_fmt", "% do total", "Qtd_ativos"]].rename(columns={
            "corretora": "Corretora",
            "PL_fmt": "PL (R$)",
            "Qtd_ativos": "Nº de ativos"
        }),
        hide_index=True,
        use_container_width=True
    )

    # Métricas rápidas
    cols = st.columns(len(por_corretora) + 1)
    cols[0].metric("Patrimônio Total", format_brl(pl_total))
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
        at = str(row.get("asset_tipo","") + str(row.get("mercado",""))).upper()
        if any(x in at for x in ["ACAO","FII","EQUITY","ETF","RV"]):
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
        
        if rv_real.empty:
            st.info("Cliente sem posições em RV Brasil no momento.")
            # Sugestão completa mesmo sem posição atual
            tickers_rv = RV_BR_ACOES + RV_BR_FIIS
            if not tickers_rv:
                st.warning("Nenhum ticker definido para RV Brasil.")
            else:
                peso_por_ativo = alvo_rv / len(tickers_rv)
                sugestao = []
                for t in tickers_rv:
                    sugestao.append([t, "R$ 0,00", format_brl(peso_por_ativo), format_brl(-peso_por_ativo)])
                
                rv_df = pd.DataFrame(sugestao, columns=["Ativo", "Atual (R$)", "Sugerido (R$)", "Diferença (R$)"])
        else:
            # Agrupar posições reais por ticker
            rv_real_group = rv_real.groupby("asset_id").agg({
                "valor_mercado": "sum",
                "asset_nome": "first"  # ou outro campo com nome amigável
            }).reset_index()
            
            # Sugestão: peso igual entre todos os tickers da lista (pode mudar depois)
            tickers_rv = RV_BR_ACOES + RV_BR_FIIS
            if not tickers_rv:
                st.warning("Lista de tickers RV Brasil vazia.")
                st.stop()
            
            peso_por_ativo = alvo_rv / len(tickers_rv)
            
            sugestao = []
            for t in tickers_rv:
                atual = rv_real_group[rv_real_group["asset_id"] == t]["valor_mercado"].sum() if t in rv_real_group["asset_id"].values else 0.0
                diff = atual - peso_por_ativo
                sugestao.append([
                    t,
                    format_brl(atual),
                    format_brl(peso_por_ativo),
                    format_brl(diff)
                ])
            
            rv_df = pd.DataFrame(sugestao, columns=["Ativo", "Atual (R$)", "Sugerido (R$)", "Diferença (R$)"])
        
        # Função de estilo corrigida (verde = comprar/faltando, vermelho = vender/excesso)
        def style_diff_rv(val):
            try:
                # Remove formatação e converte para float
                num_str = str(val).replace("R$", "").replace(".", "").replace(",", ".").strip()
                num = float(num_str)
                if num < 0:   # faltando → comprar → verde
                    return "color: #2e7d32; font-weight: 650;"
                if num > 0:   # excesso → vender → vermelho
                    return "color: #c62828; font-weight: 650;"
            except:
                pass
            return "color: #757575;"
    
        st.dataframe(
            rv_df.style.applymap(style_diff_rv, subset=["Diferença (R$)"]),
            use_container_width=True,
            hide_index=True
        )

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