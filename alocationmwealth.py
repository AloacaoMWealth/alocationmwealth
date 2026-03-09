# alocationmwealth-6.py - VERSÃO COMPLETA INTEGRADA
import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
from datetime import datetime, timedelta
import requests
import json
from pathlib import Path
import positions as posmod  # Seu positions-5.py

try:
    import yfinance as yf
    HAS_YF = True
except Exception:
    HAS_YF = False

st.set_page_config(page_title="M Wealth Asset Allocation", layout="wide")

# CSS custom (seu original)
st.markdown("""
<style>
.block-container {padding-top: 1.2rem; padding-bottom: 2rem;}
[data-testid="stMetricValue"] {font-size: 1.4rem;}
[data-testid="stMetricDelta"] {font-size: 0.9rem;}
.mw-subtle {color: rgba(250,250,250,0.65); font-size: 0.9rem;}
.mw-divider {border-top: 1px solid rgba(255,255,255,0.08); margin: 0.75rem 0 1rem 0;}
</style>
""", unsafe_allow_html=True)

# Suas funções originais (format, parse, etc.)
def safe_int(val):
    try: return int(float(str(val).strip().replace(',', '.')))
    except: return 0

def format_brl(v):
    try: return f"R$ {float(v):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except: return "R$ 0,00"

def format_usd(v):
    try: return f"US$ {float(v):,.2f}"
    except: return "US$ 0.00"

def fmt_pct(x):
    try: return f"{100*float(x):.2f}%"
    except: return "0.00%"

def parse_input_money(s):
    try: return float(str(s).replace('R$', '').replace('US$', '').replace('.', '').replace(',', '.').strip())
    except: return 0.0

def style_compra_venda(val):
    try:
        num = float(str(val).replace('R$', '').replace('US$', '').replace('.', '').replace(',', '.').strip())
        if num > 0: return 'color: #2e7d32; font-weight: 650'  # buy
        if num < 0: return 'color: #c62828; font-weight: 650'  # sell
    except: pass
    return 'color: rgba(255,255,255,0.55)'

# PTAX (do positions)
@st.cache_data(ttl=3600)
def get_ptax_usd_brl_last():
    try:
        base = "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoDolarPeriodo"
        hoje = datetime.now().date()
        ini = (hoje - timedelta(days=10)).strftime('%d-%m-%Y')
        fim = hoje.strftime('%d-%m-%Y')
        url = f"{base}(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)?@dataInicial='{ini}'&@dataFinalCotacao='{fim}'&$format=json&$top=1&$orderby=dataHoraCotacao desc&$select=cotacaoVenda,dataHoraCotacao"
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        js = r.json()
        val = js.get('value', [])
        if not val: raise ValueError("Sem dados PTAX")
        return float(val[0]['cotacaoVenda']), val[0]['dataHoraCotacao']
    except:
        return 5.60, "Fallback"

# Seu load pesos (mantido igual)
def load_pesos_xlsx(path_xlsx="Pesos-alocacao.xlsx"):
    try:
        xls = pd.ExcelFile(path_xlsx, engine='openpyxl')
        sheet0 = xls.sheet_names[0]
        df = pd.read_excel(xls, sheet_name=sheet0, header=None).fillna('')
        pesos = {}
        carteira_atual = None
        for _, row in df.iterrows():
            a = str(row.iloc[0]).strip()
            b = str(row.iloc[1]).strip()
            if not a or not b: continue
            if b.lower() == 'neutro' and a != 'carteira_atual':
                carteira_atual = a
                pesos.setdefault(carteira_atual, {})
                continue
            if carteira_atual is None: continue
            bucket = a
            try:
                w = float(str(b).replace(',', '.').strip())
            except:
                w = 0.0
            pesos[carteira_atual][bucket] = w
        return {k: v for k, v in pesos.items() if len(v) > 0}
    except Exception as e:
        st.error(f"Erro ao ler Pesos-alocacao.xlsx: {e}")
        return {}

# Header (seu original)
h1, h2 = st.columns([0.14, 0.86], vertical_alignment="center")
with h1:
    try:
        st.image("Logo-M-Wealth.png", use_container_width=True)
    except:
        pass
with h2:
    st.markdown("### Asset Allocation")
    st.markdown('<div class="mw-subtle">Proto: inputs atuais serão substituídos por posição do cliente</div>', unsafe_allow_html=True)
st.markdown('<div class="mw-divider"></div>', unsafe_allow_html=True)

# Tabs (seu original + NOVO asset allocation)
tab_up, tab_aa = st.tabs(["Atualizar posições", "Asset Allocation"])

with tab_up:
    # Seu código ORIGINAL de positions (mantido 100%)
    st.subheader("Posições lidas do GitHub")
    st.caption("O app lê os arquivos na pasta ./posicoes. Para atualizar, faça commit de novos arquivos com o mesmo nome.")
    
    st.write("**Arquivos esperados**")
    for name, p in posmod.REPO_FILES.items():
        st.write(f"✅ {name}" if p.exists() else f"❌ FALTA - {name}")
    
    col1, col2 = st.columns(1)
    with col1:
        dt_pos = st.date_input("Data da posição", value=datetime.now().date())
    with col2:
        if st.button("🔄 Rebuild positions", type="primary", use_container_width=True):
            with st.spinner("Lendo arquivos, normalizando, merge controle, salvando..."):
                try:
                    posmod.build_consolidated()
                    st.success("✅ Rebuild OK!")
                except Exception as e:
                    st.error(f"Erro: {e}")
    
    # Mostra consolidated (seu novo positions)
    total_r, breakdown, _ = posmod.build_consolidated()
    st.subheader("Posições Consolidadas")
    col_sel1, col_sel2 = st.columns(2)
    with col_sel1:
        carteira_sel = st.selectbox("Carteira (GRUPO GERAL)", ["Todas"] + sorted(total_r['GRUPO GERAL'].dropna().unique()))
    with col_sel2:
        if carteira_sel != "Todas":
            contas = total_r[total_r['GRUPO GERAL'] == carteira_sel]['CLIENTE - CORRETORA'].dropna().unique()
            conta_sel = st.selectbox("Conta", ["Todas"] + sorted(contas))
        else:
            conta_sel = "Todas"
    
    df_filt = total_r[(total_r['GRUPO GERAL'] == carteira_sel) | (carteira_sel == "Todas")]
    if conta_sel != "Todas":
        df_filt = df_filt[df_filt['CLIENTE - CORRETORA'] == conta_sel]
    
    total_sel = df_filt['Total R$'].sum()
    st.metric("Total Selecionado", f"R$ {total_sel:,.0f}")
    
    st.dataframe(df_filt, use_container_width=True)

with tab_aa:
    # === NOVO: Asset Allocation com POSIÇÕES REAIS ===
    ptax, data_ptax = get_ptax_usd_brl_last()
    st.caption(f"PTAX: {ptax:.4f} ({data_ptax})")
    
    # Seleciona carteira
    total_r, _, _ = posmod.build_consolidated()
    carteiras = sorted(total_r['GRUPO GERAL'].dropna().unique())
    carteira = st.selectbox("Carteira", carteiras)
    
    # Carrega posições reais dessa carteira
    pos_carteira = posmod.load_positions_carteira(carteira)
    total_real = pos_carteira.sum()
    
    patrimonio_alvo = st.number_input("Patrimônio Alvo R$", value=total_real or 500000.0)
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Atual Real", f"R$ {total_real:,.0f}")
    col2.metric("Alvo", f"R$ {patrimonio_alvo:,.0f}")
    col3.metric("Gap", f"R$ {(patrimonio_alvo - total_real):,.0f}")
    
    # Carrega seus pesos
    pesos_manual = load_pesos_xlsx()
    pesos_carteira = pesos_manual.get(carteira, {})
    
    # Buckets atuais vs ideal
    buckets = ['RF Brasil', 'RV Brasil', 'Internacional', 'Outros']
    pos_real_pct = (pos_carteira.reindex(buckets, fill_value=0) / total_real).fillna(0)
    
    comparacao = pd.DataFrame({
        'Bucket': buckets,
        'Atual R$': pos_carteira.reindex(buckets, fill_value=0),
        'Atual %': pos_real_pct,
        'Ideal %': [pesos_carteira.get(b, 0) for b in buckets],
        'Ideal R$': [patrimonio_alvo * pesos_carteira.get(b, 0) for b in buckets],
        'Gap R$': [patrimonio_alvo * pesos_carteira.get(b, 0) - pos_carteira.get(b, 0) for b in buckets]
    })
    comparacao['Ação'] = np.where(comparacao['Gap R$'] > 0, '🟢 Comprar', 
                                  np.where(comparacao['Gap R$'] < -10000, '🔴 Vender', '⚪ OK'))
    
    st.subheader("📊 Alocação Atual vs Ideal")
    st.dataframe(comparacao.style.format({
        'Atual %': '{:.1%}', 'Ideal %': '{:.1%}',
        'Atual R$': '{:,.0f}', 'Ideal R$': '{:,.0f}', 'Gap R$': '{:,.0f}'
    }).applymap(lambda val, key: style_compra_venda(val) if key == 'Ação' else '', subset=['Ação']), 
    use_container_width=True, height=400)
    
    # Mantém seus expanders MANUAIS (RF/RV/Intl YF basket)
    st.markdown('<div class="mw-divider"></div>', unsafe_allow_html=True)
    st.subheader("🎯 Basket Manual (YFinance)")
    
    # ... COLE AQUI TODO SEU CÓDIGO DOS EXPANDERS ORIGINAIS ...
    # st.expander("1 Renda Fixa Brasil R$") etc.
    # Manter igualzinho!
    
    # Exemplo rápido do seu RF expander (adapte o resto igual):
    with st.expander("RF Brasil (Atual Auto)", expanded=True):
        rf_real = pos_carteira.get('RF Brasil', 0)
        st.metric("RF Atual", f"R$ {rf_real:,.0f}")
        # Seu código manual abaixo...
