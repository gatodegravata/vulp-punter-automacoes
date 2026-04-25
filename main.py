import os
import time
import requests
import psycopg2
from psycopg2.extras import Json
from datetime import datetime
import pytz

# --- CONFIGURAÇÕES ---
DATABASE_URL = os.getenv('DATABASE_URL')
API_KEY = os.getenv('ODDS_API_TOKEN')
BASE_URL = "https://api.oddspapi.io/v4"

def conectar_banco():
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print(f"❌ Erro ao conectar ao banco: {e}")
        return None

def buscar_e_salvar_fixtures(data_inicio, data_fim):
    """Processo 1: Baixa a lista de jogos e popula a tabela 'jogos'"""
    print(f"\n🔍 [1/2] Buscando fixtures de {data_inicio} até {data_fim}...")
    endpoint = f"{BASE_URL}/fixtures"
    params = {
        "apiKey": API_KEY,
        "sportId": 10,
        "from": data_inicio,
        "to": data_fim,
        "hasOdds": "true"
    }

    try:
        response = requests.get(endpoint, params=params)
        if response.status_code != 200:
            print(f"❌ Erro na API Fixtures: {response.status_code}")
            return False

        jogos = response.json()
        # Garante que 'jogos' seja uma lista
        if isinstance(jogos, dict): jogos = jogos.get('data', [])

        conn = conectar_banco()
        with conn.cursor() as cur:
            for j in jogos:
                cur.execute("""
                    INSERT INTO jogos (
                        fixture_id, time_casa, time_fora, liga_nome, categoria_nome, 
                        sport_id, tournament_id, status_id, status_nome, data_inicio
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (fixture_id) DO UPDATE SET
                        status_id = EXCLUDED.status_id,
                        status_nome = EXCLUDED.status_nome,
                        atualizado_em = CURRENT_TIMESTAMP;
                """, (
                    str(j.get('fixtureId')), j.get('participant1Name'), j.get('participant2Name'),
                    j.get('tournamentName'), j.get('categoryName'), j.get('sportId'),
                    j.get('tournamentId'), j.get('statusId'), j.get('statusName'), j.get('startTime')
                ))
            conn.commit()
        conn.close()
        print(f"✅ {len(jogos)} jogos sincronizados no banco.")
        return True
    except Exception as e:
        print(f"⚠️ Erle no processamento de fixtures: {e}")
        return False

def baixar_odds_pendentes():
    """Processo 2: Busca no banco jogos sem odds e baixa da API"""
    print("\n⬇️ [2/2] Iniciando download de odds pendentes...")
    conn = conectar_banco()
    if not conn: return

    try:
        with conn.cursor() as cur:
            # Busca jogos que NÃO estão na tabela jogos_odds
            cur.execute("""
                SELECT j.fixture_id, j.time_casa, j.time_fora 
                FROM jogos j
                LEFT JOIN jogos_odds o ON j.fixture_id = o.fixture_id
                WHERE o.fixture_id IS NULL
                ORDER BY j.data_inicio ASC
            """)
            pendentes = cur.fetchall()

        print(f"📂 Encontrados {len(pendentes)} jogos sem odds no histórico.")

        for f_id, home, away in pendentes:
            print(f"--- Baixando: {home} vs {away} ({f_id})")
            
            # Request com Retry para Rate Limit
            sucesso = False
            tentativas = 0
            while tentativas < 5 and not sucesso:
                res = requests.get(f"{BASE_URL}/historical-odds", params={
                    "apiKey": API_KEY, "fixtureId": f_id, "bookmakers": "bet365"
                })

                if res.status_code == 200:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO jogos_odds (fixture_id, odds_brutas) VALUES (%s, %s)",
                            (f_id, Json(res.json()))
                        )
                        conn.commit()
                    print(f"   ✅ Salvo!")
                    sucesso = True
                elif res.status_code == 429:
                    wait = (tentativas + 1) * 10
                    print(f"   ⏳ Rate Limit! Aguardando {wait}s...")
                    time.sleep(wait)
                    tentativas += 1
                else:
                    print(f"   ❌ Erro {res.status_code}. Pulando.")
                    break
            
            time.sleep(5) # Delay educado entre jogos

    except Exception as e:
        print(f"⚠️ Erro no processamento de odds: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    # 1. Solicita as datas no terminal
    print("=== SISTEMA DE CAPTURA VULP ===")
    data_in = input("Data Início (AAAA-MM-DD): ")
    data_fi = input("Data Fim    (AAAA-MM-DD): ")

    # Formatação para o padrão da API (ISO 8601)
    # Adicionamos o fuso Z (UTC) conforme exigido
    DATA_INICIO = f"{data_in}T00:00:00Z"
    DATA_FIM = f"{data_fi}T23:59:59Z"

    # 2. Executa a sequência
    if buscar_e_salvar_fixtures(DATA_INICIO, DATA_FIM):
        baixar_odds_pendentes()
    
    print("\n🚀 Operação finalizada!")