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

def buscar_e_salvar_fixtures(data_inicio, data_fim, status_id):
    """Passo 1: Sincroniza a agenda de jogos"""
    print(f"\n🔍 [PASSO JOGOS] Buscando de {data_inicio} até {data_fim} | Status: {status_id}")
    
    endpoint = f"{BASE_URL}/fixtures"
    params = {
        "apiKey": API_KEY,
        "sportId": 10,
        "from": data_inicio,
        "to": data_fim,
        "statusId": status_id
    }

    try:
        response = requests.get(endpoint, params=params)
        if response.status_code != 200:
            print(f"❌ Erro na API Fixtures: {response.status_code} - {response.text}")
            return False

        jogos = response.json()
        if isinstance(jogos, dict): 
            jogos = jogos.get('data', [])

        if not jogos:
            print("⚠️ Nenhum jogo encontrado para estes critérios.")
            return True # Retorna True para não travar se o modo for 0

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

def baixar_odds_pendentes(bookmaker):
    """Passo 2: Baixa odds para o que está no banco sem registro de odds"""
    print(f"\n⬇️ [PASSO ODDS] Buscando na {bookmaker}...")
    conn = conectar_banco()
    if not conn: return

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT j.fixture_id, j.time_casa, j.time_fora 
                FROM jogos j
                LEFT JOIN jogos_odds o ON j.fixture_id = o.fixture_id
                WHERE o.fixture_id IS NULL
                ORDER BY j.data_inicio ASC
            """)
            pendentes = cur.fetchall()

        if not pendentes:
            print("✨ Nenhuma odd pendente no banco.")
            return

        print(f"📂 Processando {len(pendentes)} jogos...")

        for f_id, home, away in pendentes:
            print(f"--- {home} vs {away} ({f_id})")
            
            tentativas = 0
            while tentativas < 3:
                res = requests.get(f"{BASE_URL}/historical-odds", params={
                    "apiKey": API_KEY, "fixtureId": f_id, "bookmakers": bookmaker
                })

                if res.status_code == 200:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO jogos_odds (fixture_id, odds_brutas) VALUES (%s, %s)",
                            (f_id, Json(res.json()))
                        )
                        conn.commit()
                    print(f"    ✅ Salvo.")
                    break
                elif res.status_code == 429:
                    print(f"    ⏳ Rate limit. Esperando 15s...")
                    time.sleep(6)
                    tentativas += 1
                else:
                    print(f"    ⚠️ Falha (Status {res.status_code}).")
                    break
            
            time.sleep(5) # Intervalo entre chamadas

    except Exception as e:
        print(f"⚠️ Erro nas odds: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    if len(sys.argv) < 6:
        print("\n❌ Parâmetros insuficientes!")
        print("💡 Uso: python main.py [INICIO] [FIM] [STATUS] [BOOKMAKER] [MODO]")
        print("MODOS: 0=Tudo, 1=Só Jogos, 2=Só Odds")
        print("Ex: python main.py 2026-04-07 2026-04-09 2 bet365 0")
        sys.exit(1)

    d_in, d_fi, s_id, b_maker, modo = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]

    print(f"\n{'='*40}")
    print(f"🚀 VULP AUTOMATIONS - MODO {modo}")
    print(f"{'='*40}")

    DATA_INICIO = f"{d_in}T00:00:00Z"
    DATA_FIM = f"{d_fi}T23:59:59Z"

    # Lógica de Modos
    if modo in ['0', '1']:
        sucesso = buscar_e_salvar_fixtures(DATA_INICIO, DATA_FIM, s_id)
    
    if modo in ['0', '2']:
        # Se modo 0, só entra aqui se o passo 1 funcionou (ou se ignorarmos erro de 'vazio')
        baixar_odds_pendentes(b_maker)
    
    print(f"\n{'='*40}")
    print(f"🏁 PROCESSO FINALIZADO!")
    print(f"{'='*40}")