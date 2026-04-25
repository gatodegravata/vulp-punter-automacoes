import os
import sys
import time
import requests
import psycopg2
from psycopg2.extras import Json
from datetime import datetime

# --- CONFIGURAÇÕES VIA ENV ---
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
    """Passo 1: Sincroniza a agenda de jogos"""
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
            print(f"❌ Erro na API Fixtures: {response.status_code} - {response.text}")
            return False

        jogos = response.json()
        if isinstance(jogos, dict): 
            jogos = jogos.get('data', [])

        conn = conectar_banco()
        if not conn: return False
        
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
        print(f"⚠️ Erro ao processar fixtures: {e}")
        return False

def baixar_odds_pendentes():
    """Passo 2: Baixa odds apenas para o que ainda não temos no histórico"""
    print("\n⬇️ [2/2] Verificando odds pendentes no banco...")
    conn = conectar_banco()
    if not conn: return

    try:
        with conn.cursor() as cur:
            # Seleciona jogos que não possuem entrada na tabela de odds
            cur.execute("""
                SELECT j.fixture_id, j.time_casa, j.time_fora 
                FROM jogos j
                LEFT JOIN jogos_odds o ON j.fixture_id = o.fixture_id
                WHERE o.fixture_id IS NULL
                ORDER BY j.data_inicio ASC
            """)
            pendentes = cur.fetchall()

        if not pendentes:
            print("✨ Tudo atualizado! Nenhuma odd pendente encontrada.")
            return

        print(f"📂 Encontrados {len(pendentes)} jogos para baixar.")

        for f_id, home, away in pendentes:
            print(f"--- Baixando: {home} vs {away} ({f_id})")
            
            tentativas = 0
            while tentativas < 5:
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
                    print(f"   ✅ Salvo com sucesso.")
                    break
                elif res.status_code == 429:
                    wait = (tentativas + 1) * 10
                    print(f"   ⏳ Limite atingido (429). Esperando {wait}s...")
                    time.sleep(wait)
                    tentativas += 1
                elif res.status_code == 404:
                    print(f"   ⚠️ Odds ainda não disponíveis para este jogo.")
                    break
                else:
                    print(f"   ❌ Erro {res.status_code}. Pulando.")
                    break
            
            time.sleep(5) # Intervalo entre requisições

    except Exception as e:
        print(f"⚠️ Erro no processamento de odds: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    # Validação dos argumentos de data
    if len(sys.argv) == 3:
        data_in = sys.argv[1]
        data_fi = sys.argv[2]
    else:
        print("\n❌ Formato incorreto!")
        print("💡 Exemplo de uso: python main.py 2026-04-26 2026-04-30")
        sys.exit(1)

    print(f"\n{'='*40}")
    print(f"🚀 VULP AUTOMATIONS - MODO MANUAL")
    print(f"{'='*40}")

    DATA_INICIO = f"{data_in}T00:00:00Z"
    DATA_FIM = f"{data_fi}T23:59:59Z"

    if buscar_e_salvar_fixtures(DATA_INICIO, DATA_FIM):
        baixar_odds_pendentes()
    
    print(f"\n{'='*40}")
    print(f"🏁 PROCESSO FINALIZADO!")
    print(f"{'='*40}")