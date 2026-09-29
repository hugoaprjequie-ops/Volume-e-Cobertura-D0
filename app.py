import streamlit as st
import pandas as pd
import numpy as np

st.set_page_config(page_title="Dashboard Diário - Volume & Cobertura", layout="wide")

st.title("📊 Painel Diário de Vendas - GP7 Jequié")
st.markdown("Acompanhamento de **Volume (HL)**, **Cerveja**, **NAB**, **Marketplace** e **Cobertura** por Gerente e Setor.")

# Upload lateral dos arquivos
st.sidebar.header("📁 Upload das Bases")
file_pedidos = st.sidebar.file_uploader("Rotina 03.01.36.04 (Pedidos do Dia)", type=["csv"])
file_bees = st.sidebar.file_uploader("Rotina 03.01.46.06 (Pedidos BEES)", type=["csv"])
file_skus = st.sidebar.file_uploader("Base 01.11 (SKUs)", type=["csv"])

if file_pedidos and file_bees and file_skus:
    # 1. Leitura dos arquivos CSV (ajustando separador e codificação para padrão Ambev/SIV)
    df_pedidos = pd.read_csv(file_pedidos, sep=';', encoding='latin1', low_memory=False)
    df_bees = pd.read_csv(file_bees, sep=';', encoding='latin1', low_memory=False)
    df_skus = pd.read_csv(file_skus, sep=';', encoding='latin1', low_memory=False)

    # 2. Tratamento e Limpeza Básica
    df_skus.rename(columns={'Código': 'Produto', 'Fator Hectolitro': 'FHL'}, inplace=True)
    df_pedidos['Quantidade'] = pd.to_numeric(df_pedidos['Quantidade'], errors='coerce').fillna(0)
    df_pedidos['Setor'] = pd.to_numeric(df_pedidos['Setor'], errors='coerce').fillna(0)
    df_skus['FHL'] = pd.to_numeric(df_skus['FHL'], errors='coerce').fillna(1.0)

    # 3. Cruzamento com a base de SKUs para buscar Categoria e FHL
    df_merged = pd.merge(df_pedidos, df_skus[['Produto', 'Categoria', 'FHL']], on='Produto', how='left')
    df_merged['Volume_HL'] = df_merged['Quantidade'] * df_merged['FHL']

    # 4. Segmentações de Volume
    df_merged['Vol_Cerveja'] = np.where(df_merged['Categoria'].str.upper().str.contains('CERVEJA', na=False), df_merged['Volume_HL'], 0)
    df_merged['Vol_NAB'] = np.where(df_merged['Categoria'].str.upper().str.contains('NAB', na=False), df_merged['Volume_HL'], 0)
    
    # Faturamento Marketplace
    if 'Origem Pedido' in df_merged.columns:
        df_merged['Fat_Marketplace'] = np.where(df_merged['Origem Pedido'].str.upper().str.contains('MARKETPLACE', na=False), pd.to_numeric(df_merged['Total Pedido'], errors='coerce').fillna(0), 0)
    else:
        df_merged['Fat_Marketplace'] = 0

    # 5. Atribuição de Gerente de Vendas por Setor
    def define_gerente(setor):
        if 101 <= setor <= 109:
            return 'Gerente de Vendas 1'
        elif 201 <= setor <= 208:
            return 'Gerente de Vendas 2'
        else:
            return 'Outros'

    df_merged['Gerente'] = df_merged['Setor'].apply(define_gerente)

    # 6. Criação das Abas do Web App
    tab1, tab2 = st.tabs(["📦 Volume", "🎯 Cobertura"])

    with tab1:
        st.subheader("Consolidado de Volume por Gerente e Setor")
        
        resumo_vol = df_merged.groupby(['Gerente', 'Setor']).agg({
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
        
        if 'Cod. PDV' in df_merged.columns:
            resumo_cob = df_merged.groupby(['Gerente', 'Setor']).agg(
                Clientes_Positivados=('Cod. PDV', 'nunique')
            ).reset_index()
            
            st.dataframe(resumo_cob, use_container_width=True)
        else:
            st.warning("Coluna 'Cod. PDV' não encontrada para calcular a cobertura.")

else:
    st.info("👈 Por favor, faça o upload das três bases de dados (`03.01.36.04`, `03.01.46.06` e `01.11`) na barra lateral para iniciar o painel.")
