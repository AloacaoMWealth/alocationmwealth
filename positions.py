# positions-5.py (versão limpa e corrigida)
import streamlit as st
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import requests

# Paths e configs
DATADIR = Path("data")
LATEST_PARQUET = DATADIR / "positions_latest.parquet"
REPO_POS_DIR = Path("posicoes")
REPO_FILES = {
    "contas": REPO_POS_DIR / "Contas.xlsx",
    "xp": REPO_POS_DIR / "XP.xlsx", 
    "btg": REPO_POS_DIR / "BTG.xlsx",
    "cs": REPO_POS_DIR / "CSProdutos.csv"
}

@st.cache_data(ttl=3600)
def get_ptax():
    """PTAX Dólar venda mais recente"""
    try:
        url = "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)?@dataInicial='09-03-2026'&@dataFinalCotacao='09-03-2026'&format=json&$top=1&$orderby=dataHoraCotacao desc&$select=cotacaoVenda,dataHoraCotacao"
        r = requests.get(url, timeout=10)
        data = r.json()['value'][0]
        return float(data['cotacaoVenda'])
    except:
        return 5.60  # fallback

def normalize_account(x):
    if pd.isna(x): return ""
    s = str(x).strip().replace('.0', '').upper()
    return s.replace(',', '')

def normalize_btg_account(x):
    digits = ''.join(c for c in normalize_account(x) if c.isdigit())
    return digits.zfill(8) if digits else ""

def parse_btg(src):
    df = pd.read_excel(src)
    df.columns = [c.strip() for c in df.columns]
    col_conta = next((c for c in ['Conta', 'CONTA'] if c in df.columns), None)
    col_valor = next((c for c in ['Valor Bruto', 'ValorBruto'] if c in df.columns), None)
    col_produto = next((c for c in ['Produto', 'AtivoProduto'] if c in df.columns), None)
    
    df_out = pd.DataFrame({
        'corretora': 'BTG',
        'conta': df[col_conta].apply(normalize_btg_account),
        'assetid': df[col_produto].astype(str).str.strip(),
        'assetnome': df[col_produto].astype(str).str.strip(),
        'assettipo': df.get('Mercado', pd.Series(['BTG']*len(df))).astype(str).str.strip(),
        'valormercado': pd.to_numeric(df[col_valor], errors='coerce').fillna(0),
        'quantidade': 0.0,
        'moeda': 'BRL'
    })
    return df_out

def parse_cs(src):
    df = pd.read_csv(src)
    df.columns = [c.strip() for c in df.columns]
    df_out = pd.DataFrame({
        'corretora': 'CS',
        'conta': df['Account'].apply(normalize_account),
        'assetid': df.get('SymbolCUSIP', df.get('CUSIP', df.get('Symbol', ''))).astype(str).str.strip(),
        'assetnome': df.get('Name', '').astype(str).str.strip(),
        'assettipo': df.get('Security Type', '').astype(str).str.strip(),
        'valormercado': pd.to_numeric(df.get('Market Value', ''), errors='coerce').fillna(0),
        'quantidade': 0.0,
        'moeda': 'USD'
    })
    return df_out

def parse_xp(src):
    xls = pd.ExcelFile(src)
    out = []
    for sheet in xls.sheet_names:
        try:
            tmp = pd.read_excel(xls, sheet_name=sheet)
            tmp.columns = [c.strip() for c in tmp.columns]
            
            col_cliente = next((c for c in ['CodigoCliente'] if c in tmp.columns), None)
            if not col_cliente: continue
            
            col_asset = next((c for c in ['CodigoAtivo', 'NomeReduzido', 'Ativo'] if c in tmp.columns), None)
            col_valor = next((c for c in ['ValorTotalBruto', 'ValorAtual', 'ValorLiquido'] if c in tmp.columns), None)
            if not col_asset or not col_valor: continue
            
            tmp_out = pd.DataFrame({
                'corretora': 'XP',
                'conta': tmp[col_cliente].apply(normalize_account),
                'assetid': tmp[col_asset].astype(str).str.strip(),
                'assetnome': tmp.get('NomeAtivo', tmp[col_asset]).astype(str).str.strip(),
                'assettipo': sheet,
                'valormercado': pd.to_numeric(tmp[col_valor], errors='coerce').fillna(0),
                'quantidade': pd.to_numeric(tmp.get('QuantidadeCotas', 0), errors='coerce').fillna(0),
                'moeda': 'BRL'
            })
            out.append(tmp_out)
        except: continue
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()

def load_contas(src):
    df = pd.read_excel(src)
    df.columns = [c.strip() for c in df.columns]
    col_corretora = next((c for c in ['CORRETORA', 'Corretora'] if c in df.columns), None)
    col_conta = next((c for c in ['NMERO DA CONTA', 'Conta'] if c in df.columns), None)
    
    df['corretora'] = df[col_corretora].str.upper()
    df['conta'] = df[col_conta].apply(normalize_account)
    df.loc[df['corretora'] == 'BTG', 'conta'] = df.loc[df['corretora'] == 'BTG', 'conta'].apply(normalize_btg_account)
    return df[['corretora', 'conta', 'GRUPO GERAL', 'CLIENTE', 'CLIENTE - CORRETORA']]

def build_consolidated():
    missing = [name for name, p in REPO_FILES.items() if not p.exists()]
    if missing:
        st.error(f"Faltam arquivos: {', '.join(missing)}")
        st.stop()
    
    contas = load_contas(REPO_FILES['contas'])
    btg = parse_btg(REPO_FILES['btg'])
    cs = parse_cs(REPO_FILES['cs'])
    xp = parse_xp(REPO_FILES['xp'])
    
    pos = pd.concat([btg, cs, xp], ignore_index=True)
    pos = pos.merge(contas, on=['corretora', 'conta'], how='left')
    
    ptax = get_ptax()
    pos['valorr_convertida'] = np.where(pos['moeda'] == 'USD', pos['valormercado'] * ptax, pos['valormercado'])
    
    # Consolidação: total R$ + breakdown corretora
    total_r = pos.groupby(['GRUPO GERAL', 'CLIENTE - CORRETORA'])[['valorr_convertida']].sum().reset_index()
    total_r.columns = ['GRUPO GERAL', 'CLIENTE - CORRETORA', 'Total R$']
    
    breakdown = pos.groupby(['GRUPO GERAL', 'CLIENTE - CORRETORA', 'corretora', 'moeda'])[['valormercado']].sum().reset_index()
    breakdown.columns = ['GRUPO GERAL', 'CLIENTE - CORRETORA', 'Corretora', 'Moeda', 'Valor Original']
    
    total_r.to_parquet(LATEST_PARQUET)
    return total_r, breakdown, pos

# Streamlit App
st.set_page_config(page_title="M Wealth Positions", layout="wide")
st.title("📊 Posições Consolidadas")

tab1, tab2 = st.tabs(["Resumo Total R$", "Detalhe por Corretora"])

with tab1:
    total_r, _, _ = build_consolidated()
    col1, col2 = st.columns([1, 3])
    with col1:
        carteira = st.selectbox("Carteira", sorted(total_r['GRUPO GERAL'].dropna().unique()))
    df_filt = total_r[total_r['GRUPO GERAL'] == carteira]
    total_port = df_filt['Total R$'].sum()
    st.metric("Patrimônio Total", f"R$ {total_port:,.0f}")
    st.dataframe(df_filt, use_container_width=True)

with tab2:
    _, breakdown, pos_raw = build_consolidated()
    col1, col2 = st.columns([1, 3])
    with col1:
        carteira = st.selectbox("Carteira", sorted(breakdown['GRUPO GERAL'].dropna().unique()))
        conta = st.selectbox("Conta", sorted(breakdown[breakdown['GRUPO GERAL'] == carteira]['CLIENTE - CORRETORA'].dropna().unique()))
    df_break = breakdown[(breakdown['GRUPO GERAL'] == carteira) & (breakdown['CLIENTE - CORRETORA'] == conta)]
    st.dataframe(df_break, use_container_width=True)
    
    with st.expander("Posições Detalhadas"):
        pos_filt = pos_raw[(pos_raw['GRUPO GERAL'] == carteira) & (pos_raw['CLIENTE - CORRETORA'] == conta)]
        st.dataframe(pos_filt[['assetid', 'assetnome', 'corretora', 'moeda', 'valormercado', 'valorr_convertida']].sort_values('valorr_convertida', ascending=False), use_container_width=True)

st.caption(f"PTAX usada: {get_ptax():.4f} | Atualizado: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
