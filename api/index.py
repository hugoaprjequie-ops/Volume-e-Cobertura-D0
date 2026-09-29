import os
import io
import pandas as pd
import numpy as np
from flask import Flask, render_template_string, request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

app = Flask(__name__)

# ID da pasta do Google Drive fornecido
FOLDER_ID = "1Kw_oHgF0npTcXLkMKek51bKdAF0TAWca"

def get_drive_service():
    """Autentica na API do Google Drive usando variáveis de ambiente da Vercel"""
    try:
        # Na Vercel, podemos armazenar as credenciais como Variáveis de Ambiente (Environment Variables)
        # Ou ler de um secret JSON estruturado
        import json
        gcp_creds_json = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
        if gcp_creds_json:
            creds_dict = json.loads(gcp_creds_json)
            creds = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=['https://www.googleapis.com/auth/drive.readonly']
            )
            return build('drive', 'v3', credentials=creds)
    except Exception as e:
        print(f"Erro de autenticação: {e}")
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
        print(f"Erro ao baixar {file_name_pattern}: {e}")
        return None

@app.route('/')
def index():
    service = get_drive_service()
    if not service:
        return "Erro: Credenciais do Google Drive (GCP_SERVICE_ACCOUNT_JSON) não configuradas nas variáveis de ambiente da Vercel."

    df_pedidos = baixar_csv_do_drive(service, "03.01.36.04")
    df_bees = baixar_csv_do_drive(service, "03.01.46.06")
    df_skus = baixar_csv_do_drive(service, "01.11")

    if df_pedidos is None or df_bees is None or df_skus is None:
        return "Erro: Não foi possível carregar os arquivos CSV na pasta do Google Drive."

    # Processamento e Tratamento
    df_skus.rename(columns={'Código': 'Produto', 'Fator Hectolitro': 'FHL'}, inplace=True)
    df_pedidos['Quantidade'] = pd.to_numeric(df_pedidos['Quantidade'], errors='coerce').fillna(0)
    df_pedidos['Setor'] = pd.to_numeric(df_pedidos['Setor'], errors='coerce').fillna(0)
    df_skus['FHL'] = pd.to_numeric(df_skus['FHL'], errors='coerce').fillna(1.0)

    df_merged = pd.merge(df_pedidos, df_skus[['Produto', 'Categoria', 'FHL']], on='Produto', how='left')
    df_merged['Volume_HL'] = df_merged['Quantidade'] * df_merged['FHL']

    df_merged['Vol_Cerveja'] = np.where(df_merged['Categoria'].str.upper().str.contains('CERVEJA', na=False), df_merged['Volume_HL'], 0)
    df_merged['Vol_NAB'] = np.where(df_merged['Categoria'].str.upper().str.contains('NAB', na=False), df_merged['Volume_HL'], 0)
    
    if 'Origem Pedido' in df_merged.columns:
        df_merged['Fat_Marketplace'] = np.where(df_merged['Origem Pedido'].str.upper().str.contains('MARKETPLACE', na=False), pd.to_numeric(df_merged['Total Pedido'], errors='coerce').fillna(0), 0)
    else:
        df_merged['Fat_Marketplace'] = 0

    def define_gerente(setor):
        if 101 <= setor <= 109:
            return 'Gerente de Vendas 1'
        elif 201 <= setor <= 208:
            return 'Gerente de Vendas 2'
        else:
            return 'Outros'

    df_merged['Gerente'] = df_merged['Setor'].apply(define_gerente)

    # Agrupamentos
    resumo_vol = df_merged.groupby(['Gerente', 'Setor']).agg({
        'Volume_HL': 'sum',
        'Vol_Cerveja': 'sum',
        'Vol_NAB': 'sum',
        'Fat_Marketplace': 'sum'
    }).reset_index()

    resumo_cob = df_merged.groupby(['Gerente', 'Setor']).agg(
        Clientes_Positivados=('Cod. PDV', 'nunique')
    ).reset_index() if 'Cod. PDV' in df_merged.columns else pd.DataFrame()

    # HTML de Resposta com Abas Simples (Bootstrap)
    html_template = """
    <!doctype html>
    <html lang="pt-br">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Painel Diário - GP7 Jequié</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
      </head>
      <body class="bg-light">
        <div class="container py-4">
          <h1 class="mb-4 text-dark">📊 Painel Diário de Vendas - GP7 Jequié</h1>
          
          <ul class="nav nav-tabs" id="myTab" role="tablist">
            <li class="nav-item" role="presentation">
              <button class="nav-link active" id="volume-tab" data-bs-toggle="tab" data-bs-target="#volume" type="button" role="tab">📦 Volume</button>
            </li>
            <li class="nav-item" role="presentation">
              <button class="nav-link" id="cobertura-tab" data-bs-toggle="tab" data-bs-target="#cobertura" type="button" role="tab">🎯 Cobertura</button>
            </li>
          </ul>
          
          <div class="tab-content bg-white p-4 border border-top-0 rounded-bottom shadow-sm" id="myTabContent">
            <div class="tab-pane fade show active" id="volume" role="tabpanel">
              <h3 class="mb-3">Consolidado de Volume</h3>
              {{ table_vol | safe }}
            </div>
            <div class="tab-pane fade" id="cobertura" role="tabpanel">
              <h3 class="mb-3">Cobertura Diária (Clientes Positivados)</h3>
              {{ table_cob | safe }}
            </div>
          </div>
        </div>
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
      </body>
    </html>
    """

    return render_template_string(
        html_template, 
        table_vol=resumo_vol.to_html(classes='table table-striped table-bordered', index=False, float_format=lambda x: f"{x:,.2f}"),
        table_cob=resumo_cob.to_html(classes='table table-striped table-bordered', index=False) if not resumo_cob.empty else "<p>Coluna de PDV não encontrada.</p>"
    )

if __name__ == '__main__':
    app.run(debug=True)
