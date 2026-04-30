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
    """Passo 1: Sincroniza a agenda de jogos completa"""
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
            print("⚠️ Nenhum jogo encontrado.")
            return True

        conn = conectar_banco()
        if not conn: return False
        
        with conn.cursor() as cur:
            for j in jogos:
                cur.execute("""
                    INSERT INTO jogos (
                        fixture_id, participant1_id, participant1_name, participant1_short_name, participant1_abbr,
                        participant2_id, participant2_name, participant2_short_name, participant2_abbr,
                        sport_id, sport_name, tournament_id, tournament_name, tournament_slug,
                        category_name, category_slug, season_id, status_id, status_name,
                        has_odds, start_time, true_start_time, true_end_time, api_updated_at,
                        external_providers, atualizado_em
                    ) VALUES (
                        %(fixtureId)s, %(participant1Id)s, %(participant1Name)s, %(participant1ShortName)s, %(participant1Abbr)s,
                        %(participant2Id)s, %(participant2Name)s, %(participant2ShortName)s, %(participant2Abbr)s,
                        %(sportId)s, %(sportName)s, %(tournamentId)s, %(tournamentName)s, %(tournamentSlug)s,
                        %(categoryName)s, %(categorySlug)s, %(seasonId)s, %(statusId)s, %(statusName)s,
                        %(hasOdds)s, %(startTime)s, %(trueStartTime)s, %(trueEndTime)s, %(updatedAt)s,
                        %(externalProviders)s, CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (fixture_id) DO UPDATE SET
                        status_id = EXCLUDED.status_id,
                        status_name = EXCLUDED.status_name,
                        has_odds = EXCLUDED.has_odds,
                        api_updated_at = EXCLUDED.api_updated_at,
                        external_providers = EXCLUDED.external_providers,
                        atualizado_em = CURRENT_TIMESTAMP;
                """, {
                    'fixtureId': str(j.get('fixtureId')),
                    'participant1Id': j.get('participant1Id'),
                    'participant1Name': j.get('participant1Name'),
                    'participant1ShortName': j.get('participant1ShortName'),
                    'participant1Abbr': j.get('participant1Abbr'),
                    'participant2Id': j.get('participant2Id'),
                    'participant2Name': j.get('participant2Name'),
                    'participant2ShortName': j.get('participant2ShortName'),
                    'participant2Abbr': j.get('participant2Abbr'),
                    'sportId': j.get('sportId'),
                    'sportName': j.get('sportName'),
                    'tournamentId': j.get('tournamentId'),
                    'tournamentName': j.get('tournamentName'),
                    'tournamentSlug': j.get('tournamentSlug'),
                    'categoryName': j.get('categoryName'),
                    'categorySlug': j.get('categorySlug'),
                    'seasonId': str(j.get('seasonId')) if j.get('seasonId') else None,
                    'statusId': j.get('statusId'),
                    'statusName': j.get('statusName'),
                    'hasOdds': j.get('hasOdds'),
                    'startTime': j.get('startTime'),
                    'trueStartTime': j.get('trueStartTime'),
                    'trueEndTime': j.get('trueEndTime'),
                    'updatedAt': j.get('updatedAt'),
                    'externalProviders': Json(j.get('externalProviders', {}))
                })
            conn.commit()
        conn.close()
        print(f"✅ {len(jogos)} jogos sincronizados no banco.")
        return True
    except Exception as e:
        print(f"⚠️ Erro ao processar fixtures: {e}")
        return False

def baixar_odds_pendentes(bookmaker, data_inicio, data_fim):
    print(f"\n⬇️ [PASSO ODDS] Buscando na {bookmaker} entre {data_inicio} e {data_fim}...")
    conn = conectar_banco()
    if not conn: return

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT j.fixture_id, j.participant1_name, j.participant2_name 
                FROM jogos j
                LEFT JOIN jogos_odds o ON j.fixture_id = o.fixture_id
                WHERE (
                    o.fixture_id IS NULL 
                    OR (
                        j.start_time <= NOW() 
                        AND o.atualizado_em < j.start_time
                    )
                )
                AND j.start_time >= %s 
                AND j.start_time <= %s
                AND j.participant1_name NOT LIKE '%%SRL%%'
                ORDER BY j.start_time ASC
            """, (data_inicio, data_fim))
            pendentes = cur.fetchall()

        if not pendentes:
            print("✨ Nenhuma odd pendente no banco.")
            return

        print(f"📂 Processando {len(pendentes)} jogos...")

        for f_id, home, away in pendentes:
            print(f"--- {home} vs {away} ({f_id})")
            
            tentativas = 0
            atraso_429 = 60

            while tentativas < 3:
                try:
                    res = requests.get(f"{BASE_URL}/historical-odds", params={
                        "apiKey": API_KEY, "fixtureId": f_id, "bookmakers": bookmaker
                    }, timeout=20)
                except Exception as e:
                    print(f"    ❌ Erro de conexão: {e}. Tentando novamente...")
                    tentativas += 1
                    time.sleep(5)
                    continue

                if res.status_code == 200:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO jogos_odds (fixture_id, odds_brutas, criado_em, atualizado_em) 
                            VALUES (%s, %s, NOW(), NOW())
                            ON CONFLICT (fixture_id) 
                            DO UPDATE SET 
                                odds_brutas = EXCLUDED.odds_brutas,
                                atualizado_em = NOW();
                        """, (f_id, Json(res.json())))
                        conn.commit()
                    print(f"    ✅ Salvo/Atualizado.")
                    break

                elif res.status_code == 404:
                    print(f"    ⚠️ 404: Sem cotação na API. Marcando para ignorar.")
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO jogos_odds (fixture_id, odds_brutas, criado_em, atualizado_em) 
                            VALUES (%s, %s, NOW(), NOW())
                            ON CONFLICT (fixture_id) 
                            DO UPDATE SET 
                                odds_brutas = EXCLUDED.odds_brutas,
                                atualizado_em = NOW();
                        """, (f_id, Json({"status": 404, "msg": "Sem odds na API"})))
                        conn.commit()
                    break

                elif res.status_code == 429:
                    print(f"    ⏳ RATE LIMIT! Pausando {atraso_429}s...")
                    time.sleep(atraso_429)
                    atraso_429 *= 2
                    tentativas += 1
                    if tentativas == 3: sys.exit(1)

                else:
                    print(f"    ❌ Falha (Status {res.status_code}). Tentativa {tentativas+1}/3")
                    time.sleep(10)
                    tentativas += 1
            
            time.sleep(5) 

    except Exception as e:
        print(f"⚠️ Erro inesperado no processamento: {e}")
    finally:
        if conn: conn.close()

if __name__ == "__main__":
    if len(sys.argv) < 6:
        print("\n💡 Uso: python main.py [INICIO] [FIM] [STATUS] [BOOKMAKER] [MODO]")
        sys.exit(1)

    d_in, d_fi, s_id, b_maker, modo = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]

    DATA_INICIO = f"{d_in}T00:00:00Z"
    DATA_FIM = f"{d_fi}T23:59:59Z"

    if modo in ['0', '1']:
        buscar_e_salvar_fixtures(DATA_INICIO, DATA_FIM, s_id)
    
    if modo in ['0', '2']:
        baixar_odds_pendentes(b_maker, DATA_INICIO, DATA_FIM) 
    
    print(f"\n🏁 PROCESSO FINALIZADO!")