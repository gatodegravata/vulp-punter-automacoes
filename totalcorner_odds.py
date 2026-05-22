import os
import time
import pandas as pd
import requests
import random
import logging
from bs4 import BeautifulSoup

# Configuração do Sistema de Log Nativo
logging.basicConfig(
    filename='scraper.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

########## CONFIGURAÇÕES ##############
LIMITE_URLS = 140000       # Defina como None se quiser rodar as 150k linhas direto
INPUT_FILE = "https://raw.githubusercontent.com/gatodegravata/vulp-stats/refs/heads/main/lists/jogos_2023_tc.csv"
OUTPUT_FULL_FILE = "updated/jogos_2023_tc_atualizado.csv"  # Caminho local corrigido para VPS

FILTRAR_POR_DATA = False
DATA_INICIO = "2026-02-01"  

FILTRAR_POR_LIGA = False  
LIGAS_INTERESSE = ['England League 1']

BAIXAR_MINUTOS_GOAL = True

def parse_odds_html(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    odds_data = {}

    tables = soup.find_all('table', class_='table-bordered table-condensed-2')
    for table in tables:
        tds = table.find_all('td', attrs={'data-odds': True})
        for td in tds:
            data_type = td.get('data-type')
            sub_type = td.get('data-sub_type')
            line = td.get('data-line')
            odds = td.get('data-odds')

            if not data_type or not sub_type:
                continue

            if line:
                odds_data[f"{data_type}_{sub_type}_line"] = line
                odds_data[f"{data_type}_{sub_type}_odds"] = odds
            else:
                odds_data[f"{data_type}_{sub_type}_odds"] = odds

    small_tag = soup.find('small')
    if small_tag:
        dt_text = small_tag.get_text(strip=True)
        parts = dt_text.split()
        if len(parts) >= 1: odds_data['Match Date'] = parts[0]
        if len(parts) >= 2: odds_data['Match Time'] = parts[1]

    items = soup.find_all('div', class_='score-bar-item')
    seen_stats = set()
    for item in items:
        center = item.find('div', class_='small-6 text-center columns')
        if not center: continue
        name = center.get_text(strip=True)

        if name not in ['Shoot on target', 'Shoot off target', 'Attack', 'Dangerous Attack', 'Possession %']:
            continue

        left = item.find('div', class_='small-2 text-left columns')
        right = item.find('div', class_='small-2 text-right columns')

        if left and right:
            vh = left.get_text(strip=True)
            va = right.get_text(strip=True)
            base = name.replace('%', '').strip().replace(' ', '_').lower()
            if base not in seen_stats:
                odds_data[f'{base}_home'] = vh
                odds_data[f'{base}_away'] = va
                seen_stats.add(base)
            else:
                odds_data[f'half_{base}_home'] = vh
                odds_data[f'half_{base}_away'] = va

    if BAIXAR_MINUTOS_GOAL:
        goal_minutes = []
        list_items = soup.find_all('li', class_='list-group-item')
        for li in list_items:
            text = li.get_text(strip=True)
            if 'Goal -' in text and "'" in text:
                goal_minutes.append(text)

        odds_data['GoalMinutes'] = " | ".join(goal_minutes) if goal_minutes else ""

    odds_data['merged'] = 2
    return odds_data

def scrape_match_odds(df_full, df_to_scrape, session, output_path):
    total = len(df_to_scrape)
    batch_results = []
    
    for i, (index, row) in enumerate(df_to_scrape.iterrows(), 1):
        # Tenta buscar a coluna 'Match ID', se não encontrar usa o índice do dataframe
        match_id = row.get('Match ID', index)
        url_stats = row.get('url_stats')

        if isinstance(url_stats, str) and not url_stats.startswith('http'):
            url_stats = f"https://www.totalcorner.com{url_stats}"

        if pd.isna(url_stats) or not str(url_stats).strip():
            batch_results.append({'merged': 0, 'original_index': index})
            logging.warning(f"Match ID {match_id} ignorado devido a URL vazia.")
            continue

        print(f"[{i}/{total}] Coletando Match ID {match_id}: {url_stats}")

        try:
            max_tentativas = 3
            tentativa = 0
            response = None

            while tentativa < max_tentativas:
                try:
                    response = session.get(url_stats, timeout=10)

                    if (
                        "Cloudflare" in response.text
                        or "captcha" in response.text.lower()
                        or response.status_code in [403, 429]
                    ):
                        tentativa += 1
                        print(f"[!] Bloqueio detectado. Esperando 30s... ({tentativa}/{max_tentativas})")
                        time.sleep(30)
                        continue
                    break

                except requests.exceptions.RequestException:
                    tentativa += 1
                    print(f"[!] Erro de conexão. Esperando 30s... ({tentativa}/{max_tentativas})")
                    time.sleep(30)

            if tentativa == max_tentativas:
                print(f"[!] Falha crítica após {max_tentativas} tentativas: {url_stats}")
                logging.error(f"Falha por excesso de tentativas - Match ID: {match_id}")
                batch_results.append({'merged': 0, 'original_index': index})
                continue

            response.raise_for_status()
            odds_dict = parse_odds_html(response.text)
            odds_dict['original_index'] = index
            batch_results.append(odds_dict)
            
            # Registra o sucesso no arquivo de log externo
            logging.info(f"Sucesso - Match ID: {match_id}")

        except Exception as e:
            print(f"Erro no link {url_stats}: {e}")
            logging.error(f"Erro inesperado - Match ID: {match_id} - Detalhes: {e}")
            batch_results.append({'merged': 0, 'original_index': index})

        # --- SALVAMENTO EM LOTE (A cada 20 requisições bem-sucedidas ou no final) ---
        if len(batch_results) >= 20 or i == total:
            df_batch = pd.DataFrame(batch_results)
            if not df_batch.empty:
                df_batch.set_index('original_index', inplace=True)
                
                # Mescla dados novos mantendo a integridade estrutural do Pandas
                df_full.update(df_batch)
                new_cols = df_batch.columns.difference(df_full.columns)
                df_full = df_full.join(df_batch[new_cols])
                
                # Consolida o arquivo fisicamente na VPS
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                df_full.to_csv(output_path, index=False, sep=';')
                print(f"💾 [Checkpoint] Progresso salvo em disco com segurança!")
            
            batch_results = [] # Reseta o buffer do lote

        time.sleep(random.uniform(2.1, 2.9))

    return df_full

# =========================
# EXECUÇÃO PRINCIPAL
# =========================
if __name__ == '__main__':
    # SISTEMA DE RETOMADA: Se já houver progresso salvo localmente na VPS, carrega ele
    if os.path.exists(OUTPUT_FULL_FILE):
        print(f"🔄 Arquivo de checkpoint encontrado! Retomando progresso de: {OUTPUT_FULL_FILE}")
        df_full = pd.read_csv(OUTPUT_FULL_FILE, sep=';', encoding='utf-8-sig', dtype=str)
    else:
        print(f"📥 Iniciando nova sessão de scraping. Baixando base de dados original...")
        df_full = pd.read_csv(INPUT_FILE, sep=None, engine='python', encoding='utf-8-sig')

    # Limpeza profunda nos nomes das colunas
    df_full.columns = [str(c).strip().replace('"', '').replace("'", "") for c in df_full.columns]

    if 'merged' not in df_full.columns:
        df_full['merged'] = 0

    df_full['merged'] = pd.to_numeric(df_full['merged'], errors='coerce').fillna(0)

    # Filtro inteligente: ignora o que já foi marcado como processado (2, 3 ou 4) nas sessões anteriores
    mask_to_scrape = (
        (df_full['Status'].astype(str).str.strip().str.lower() != 'full') |
        (~df_full['merged'].isin([2, 3, 4]))
    )

    if FILTRAR_POR_LIGA and LIGAS_INTERESSE:
        ligas_lower = [liga.strip().lower() for liga in LIGAS_INTERESSE]
        mask_to_scrape &= (df_full['League'].astype(str).str.strip().str.lower().isin(ligas_lower))
        print(f"🔍 Modo: FILTRADO POR LIGA ({len(LIGAS_INTERESSE)} ligas)")
    else:
        print("🌍 Modo: GLOBAL (Todas as ligas)")

    if FILTRAR_POR_DATA:
        COLUNA_DATA = 'Date'
        datas_convertidas = pd.to_datetime(df_full[COLUNA_DATA], errors='coerce')
        data_limite = pd.to_datetime(DATA_INICIO)
        mask_to_scrape &= (datas_convertidas >= data_limite)
        print(f"📅 Filtro de Data ativado: >= {DATA_INICIO}")

    df_work = df_full[mask_to_scrape].copy()
    print(f"🔎 Jogos pendentes restantes para processar: {len(df_work)}")
    
    if LIMITE_URLS is not None:
        df_work = df_work.head(LIMITE_URLS)
        print(f"⚡ Limite de processamento para esta execução: {len(df_work)} jogos")

    if df_work.empty:
        print("✅ Tudo atualizado! Nenhum registro novo pendente para baixar.")
    else:
        print(f"--- Iniciando scraping de {len(df_work)} jogos ---")

        session = requests.Session()
        session.headers.update({'User-Agent': 'Mozilla/5.0'})

        try:
            session.get('https://www.totalcorner.com/user/choose_timezone/Canada/Atlantic')
        except: 
            pass

        # Executa o loop persistindo dados em tempo de execução
        df_full = scrape_match_odds(df_full, df_work, session, OUTPUT_FULL_FILE)

        print(f"\n🏁 Processo finalizado com sucesso. Base salva em: {OUTPUT_FULL_FILE}")
        print(f"Registros com dados consolidados (merged=2): {len(df_full[df_full['merged'] == 2])}")