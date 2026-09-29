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

def baixar_csv_do_drive(service, file_name_pattern):
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
        st.error(f"Erro ao baixar {file_name_pattern}: {e}")
        return None

if st.button("🔄 Atualizar Dados do Google Drive"):
    st.cache_data.clear()
    st.rerun()

service = get_drive_service()

if service:
    with st.spinner("Buscando dados atualizados no Google Drive..."):
        df_pedidos = baixar_csv_do_drive(service, "03.01.36.04")
        df_bees = baixar_csv_do_drive(service, "03.01.46.06")
        df_skus = baixar_csv_do_drive(service, "01.11")

    if df_pedidos is not None and df_skus is not None:
        
        # --- TRATAMENTO E PADRONIZAÇÃO ---
        df_skus.rename(columns={'Código': 'Produto', 'Fator Hectolitro': 'FHL'}, inplace=True)
        df_pedidos['Quantidade'] = pd.to_numeric(df_pedidos['Quantidade'], errors='coerce').fillna(0)
        df_pedidos['Setor'] = pd.to_numeric(df_pedidos['Setor'], errors='coerce').fillna(0)
        
        # Correção do FHL (Dividindo por 10 para ajustar a escala exata solicitada)
        df_skus['FHL'] = pd.to_numeric(df_skus['FHL'], errors='coerce').fillna(1.0) / 10.0

        # Cruzamento com SKUs na rotina do dia
        df_merged = pd.merge(df_pedidos, df_skus[['Produto', 'Categoria', 'FHL']], on='Produto', how='left')
        df_merged['Volume_HL'] = df_merged['Quantidade'] * df_merged['FHL']

        # Segmentações de Categoria
        df_merged['Vol_Cerveja'] = np.where(df_merged['Categoria'].str.upper().str.contains('CERVEJA', na=False), df_merged['Volume_HL'], 0)
        df_merged['Vol_NAB'] = np.where(df_merged['Categoria'].str.upper().str.contains('NAB', na=False), df_merged['Volume_HL'], 0)

        # Faturamento Marketplace unificando as colunas das rotinas (03.01.36.04 e 03.01.46.06 do BEES)
        # Verificando Total Pedido na rotina 04 e Valor Total Pedido na rotina BEES
        if 'Total Pedido' in df_merged.columns and 'Origem Pedido' in df_merged.columns:
            df_merged['Fat_Marketplace'] = np.where(
                df_merged['Origem Pedido'].str.upper().str.contains('MARKETPLACE', na=False), 
                pd.to_numeric(df_merged['Total Pedido'], errors='coerce').fillna(0), 0
            )
        else:
            df_merged['Fat_Marketplace'] = 0

        # Tratando também os pedidos do BEES caso estejam presentes para somar ao Marketplace / Volume
        if df_bees is not None and not df_bees.empty:
            if 'Valor Total Pedido' in df_bees.columns and 'Origem Pedido' in df_bees.columns:
                df_bees['Fat_Marketplace'] = np.where(
                    df_bees['Origem Pedido'].str.upper().str.contains('MARKETPLACE', na=False),
                    pd.to_numeric(df_bees['Valor Total Pedido'], errors='coerce').fillna(0), 0
                )
            else:
                df_bees['Fat_Marketplace'] = 0

        # Atribuição de Gerente de Vendas
        def define_gerente(setor):
            if 101 <= setor <= 109:
                return 'Gerente de Vendas 1'
            elif 201 <= setor <= 208:
                return 'Gerente de Vendas 2'
            else:
                return 'Outros'

        df_merged['Gerente'] = df_merged['Setor'].apply(define_gerente)

        # --- FILTRO EM SUSPENSÃO (EXPANDER) ---
        with st.expander("🔍 Filtros Avançados (Gerente / Setor)", expanded=False):
            col_f1, col_f2 = st.columns(2)
            gerentes_disponiveis = sorted(df_merged['Gerente'].unique())
            gerente_selecionado = col_f1.multiselect("Filtrar por Gerente de Vendas", options=gerentes_disponiveis, default=gerentes_disponiveis)
            
            setores_disponiveis = sorted(df_merged['Setor'].unique())
            setor_selecionado = col_f2.multiselect("Filtrar por Setor", options=setores_disponiveis, default=setores_disponiveis)

        # Aplicando filtros
        df_filtrado = df_merged[
            (df_merged['Gerente'].isin(gerente_selecionado)) & 
            (df_merged['Setor'].isin(setor_selecionado))
        ]

        # --- CARDS DE KPIs (TOPO) ---
        tot_vol = df_filtrado['Volume_HL'].sum()
        tot_cerveja = df_filtrado['Vol_Cerveja'].sum()
        tot_nab = df_filtrado['Vol_NAB'].sum()
        
        # Somando faturamento marketplace considerando a base filtrada + BEES se houver correspondência
        tot_mkt = df_filtrado['Fat_Marketplace'].sum()
        if df_bees is not None and 'Fat_Marketplace' in df_bees.columns:
            tot_mkt += df_bees['Fat_Marketplace'].sum()

        st.markdown("---")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("📦 Volume Total", f"{tot_vol:,.2f} HL")
        col2.metric("🍺 Volume Cerveja", f"{tot_cerveja:,.2f} HL")
        col3.metric("🥤 Volume NAB", f"{tot_nab:,.2f} HL")
        col4.metric("🛒 Fat. Marketplace", f"R$ {tot_mkt:,.2f}")
        st.markdown("---")

        # --- ABAS DO APP ---
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
                'Volume_HL': '{:.2f} HL',
                'Vol_Cerveja': '{:.2f} HL',
                'Vol_NAB': '{:.2f} HL',
                'Fat_Marketplace': 'R$ {:,.2f}'
            }), use_container_width=True)

        with tab2:
            st.subheader("Cobertura Diária (Clientes Positivados por Setor)")
            
            if 'Cod. PDV' in df_filtrado.columns:
                resumo_cob = df_filtrado.groupby(['Gerente', 'Setor']).agg(
                    Clientes_Positivados=('Cod. PDV', 'nunique')
                ).reset_index()
                
                st.dataframe(resumo_cob, use_container_width=True)
            else:
                st.warning("Coluna 'Cod. PDV' não encontrada para calcular a cobertura.")
    else:
        st.error("⚠ Não foi possível localizar os arquivos CSV na pasta do Google Drive.")
else:
    st.warning("⚙️ Credenciais do Google Drive não configuradas.")
