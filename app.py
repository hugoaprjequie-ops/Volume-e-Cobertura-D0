import streamlit as st
import pandas as pd
import numpy as np
import io
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

st.set_page_config(page_title="Dashboard Diário - Volume & Cobertura", layout="wide")

st.title("📊 Painel Diário de Vendas - GP7 Jequié")
st.markdown("Acompanhamento de **Volume (HL)**, **Cerveja**, **NAB**, **Marketplace** e **Cobertura** integrados do Google Drive.")

FOLDER_ID = "1Kw_oHgF0npTcXLkMKek51bKdAF0TAWca"

@st.cache_resource
def get_drive_service():
    try:
        if "gcp_service_account" in st.secrets:
            creds_dict = dict(st.secrets["gcp_service_account"])
            creds = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=['https://www.googleapis.com/auth/drive.readonly']
            )
            return build('drive', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"Erro ao autenticar com as credenciais do Google Drive: {e}")
    return None

@st.cache_data(ttl=1800)
def carregar_dados_do_drive():
    service = get_drive_service()
    if not service:
        return None, None, None

    def baixar_csv_do_drive(file_name_pattern):
        try:
            query = f"'{FOLDER_ID}' in parents and name contains '{file_name_pattern}' and trashed = false"
            results = service.files().list(q=query, fields="files(id, name)").execute()
            files = results.get('files', [])
            if not files:
                return None
            
            file_id = files[0]['id']
            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                
            fh.seek(0)
            return pd.read_csv(fh, sep=';', encoding='latin1', low_memory=False)
        except Exception as e:
            return None

    df_pedidos = baixar_csv_do_drive("03.01.36.04")
    df_bees = baixar_csv_do_drive("03.01.46.06")
    df_skus = baixar_csv_do_drive("01.11")
    
    return df_pedidos, df_bees, df_skus

col_b1, _ = st.columns([0.2, 0.8])
if col_b1.button("🔄 Atualizar Cache / Drive"):
    st.cache_data.clear()
    st.rerun()

with st.spinner("Carregando e processando bases de dados..."):
    df_pedidos, df_bees, df_skus = carregar_dados_do_drive()

if df_pedidos is not None and not df_pedidos.empty and df_skus is not None:
    
    # Função auxiliar para limpar valores numéricos
    def limpar_numerico(serie):
        if serie.dtype == object:
            return pd.to_numeric(serie.astype(str).str.replace('.', '', regex=False).str.replace(',', '.', regex=False), errors='coerce').fillna(0)
        return pd.to_numeric(serie, errors='coerce').fillna(0)

    df_pedidos['Setor'] = pd.to_numeric(df_pedidos['Setor'], errors='coerce').fillna(0)
    df_pedidos['Quantidade'] = limpar_numerico(df_pedidos['Quantidade'])

    # Padronização de chaves de texto para evitar falha no merge
    df_pedidos['Produto_Key'] = df_pedidos['Produto'].astype(str).str.strip()
    df_skus.rename(columns={'Código': 'Produto', 'Fator Hectolitro': 'FHL', 'Categoria': 'Categoria'}, inplace=True)
    df_skus['Produto_Key'] = df_skus['Produto'].astype(str).str.strip()
    df_skus['FHL'] = limpar_numerico(df_skus['FHL'])

    # CRUZAMENTO ROBUSTO
    df_merged = pd.merge(df_pedidos, df_skus[['Produto_Key', 'Categoria', 'FHL']], on='Produto_Key', how='left')
    
    # Se o FHL vier nulo, tenta usar 1.0 para não zerar a quantidade
    df_merged['FHL'] = df_merged['FHL'].fillna(1.0)

    # CÁLCULO DE VOLUME (HL) = Quantidade x FHL (ou Volume Total nativo se preferir)
    df_merged['Volume_HL'] = df_merged['Quantidade'] * df_merged['FHL']
    
    # Se o cálculo por quantidade der 0 mas existir Volume Total na base, usa o volume nativo
    if 'Volume Total' in df_merged.columns:
        vol_nativo = limpar_numerico(df_merged['Volume Total'])
        df_merged['Volume_HL'] = np.where(df_merged['Volume_HL'] == 0, vol_nativo, df_merged['Volume_HL'])

    # Segmentações de Categoria
    cat_series = df_merged['Categoria'].fillna("").astype(str).str.upper()
    df_merged['Vol_Cerveja'] = np.where(cat_series.str.contains('CERVEJA', na=False), df_merged['Volume_HL'], 0)
    df_merged['Vol_NAB'] = np.where(cat_series.str.contains('NAB', na=False) | cat_series.str.contains('REFRIGERANTE', na=False), df_merged['Volume_HL'], 0)

    # Faturamento Marketplace
    if 'Total Pedido' in df_merged.columns and 'Origem Pedido' in df_merged.columns:
        origem_p = df_merged['Origem Pedido'].fillna("").astype(str).str.upper()
        tot_p = limpar_numerico(df_merged['Total Pedido'])
        df_merged['Fat_Marketplace'] = np.where(origem_p.str.contains('MARKETPLACE', na=False), tot_p, 0)
    else:
        df_merged['Fat_Marketplace'] = 0

    fat_bees_total = 0.0
    if df_bees is not None and not df_bees.empty:
        if 'Valor Total Pedido' in df_bees.columns and 'Origem Pedido' in df_bees.columns:
            origem_b = df_bees['Origem Pedido'].fillna("").astype(str).str.upper()
            val_bees = limpar_numerico(df_bees['Valor Total Pedido'])
            fat_bees_total = np.where(origem_b.str.contains('MARKETPLACE', na=False), val_bees, 0).sum()

    # Gerente por Setor (G1: 101-109 | G2: 201-208)
    def define_gerente(setor):
        if 101 <= setor <= 109:
            return 'Gerente de Vendas 1'
        elif 201 <= setor <= 208:
            return 'Gerente de Vendas 2'
        else:
            return 'Outros'

    df_merged['Gerente'] = df_merged['Setor'].apply(define_gerente)

    # Filtros em Suspensão
    with st.expander("🔍 Filtros Avançados (Gerente / Setor)", expanded=True):
        col_f1, col_f2 = st.columns(2)
        gerentes_disponiveis = sorted(df_merged['Gerente'].unique())
        gerente_selecionado = col_f1.multiselect("Filtrar por Gerente de Vendas", options=gerentes_disponiveis, default=gerentes_disponiveis)
        
        setores_disponiveis = sorted(df_merged['Setor'].unique())
        setor_selecionado = col_f2.multiselect("Filtrar por Setor", options=setores_disponiveis, default=setores_disponiveis)

    df_filtrado = df_merged[
        (df_merged['Gerente'].isin(gerente_selecionado)) & 
        (df_merged['Setor'].isin(setor_selecionado))
    ]

    # KPIs
    tot_vol = df_filtrado['Volume_HL'].sum()
    tot_cerveja = df_filtrado['Vol_Cerveja'].sum()
    tot_nab = df_filtrado['Vol_NAB'].sum()
    tot_mkt = df_filtrado['Fat_Marketplace'].sum() + fat_bees_total

    st.markdown("---")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("📦 Volume Total", f"{tot_vol:,.1f} HL")
    col2.metric("🍺 Volume Cerveja", f"{tot_cerveja:,.1f} HL")
    col3.metric("🥤 Volume NAB", f"{tot_nab:,.1f} HL")
    col4.metric("🛒 Fat. Marketplace", f"R$ {tot_mkt:,.2f}")
    st.markdown("---")

    tab1, tab2 = st.tabs(["📦 Volume", "🎯 Cobertura"])

    with tab1:
        st.subheader("Consolidado de Volume por Gerente e Setor")
        resumo_vol = df_filtrado.groupby(['Gerente', 'Setor']).agg({
            'Volume_HL': 'sum',
            'Vol_Cerveja': 'sum',
            'Vol_NAB': 'sum',
            'Fat_Marketplace': 'sum'
        }).reset_index()

        st.dataframe(resumo_vol.style.format({
            'Volume_HL': '{:.1f} HL',
            'Vol_Cerveja': '{:.1f} HL',
            'Vol_NAB': '{:.1f} HL',
            'Fat_Marketplace': 'R$ {:,.2f}'
        }), use_container_width=True)

    with tab2:
        st.subheader("Cobertura Diária (Clientes Positivados por Setor)")
        pdv_col = 'Cod. PDV' if 'Cod. PDV' in df_filtrado.columns else ('Cliente' if 'Cliente' in df_filtrado.columns else None)
        if pdv_col:
            resumo_cob = df_filtrado.groupby(['Gerente', 'Setor']).agg(
                Clientes_Positivados=(pdv_col, 'nunique')
            ).reset_index()
            st.dataframe(resumo_cob, use_container_width=True)
        else:
            st.warning("Coluna de identificação de cliente/PDV não encontrada.")
else:
    st.error("⚠ Não foi possível carregar os dados ou a base de SKUs. Verifique os arquivos CSV no Google Drive.")
