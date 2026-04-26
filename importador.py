import os
import requests
import psycopg2
from psycopg2.extras import Json
import time

# Configurações
DATABASE_URL = os.getenv('DATABASE_URL')
# Se seu repo for privado, você precisará de um Token (GITHUB_TOKEN)
GITHUB_BASE_URL = "https://api.github.com/repos/gatodegravata/vulp-api-oddsapi.io/contents"
RAW_URL_BASE = "https://raw.githubusercontent.com/gatodegravata/vulp-api-oddsapi.io/main"

def conectar():
    return psycopg2.connect(DATABASE_URL)

def listar_arquivos_github(caminho_repo):
    """Lista os nomes dos arquivos JSON em uma pasta do GitHub"""
    url = f"{GITHUB_BASE_URL}/{caminho_repo}"
    res = requests.get(url)
    if res.status_code == 200:
        return [f['name'] for f in res.json() if f['name'].endswith('.json')]
    print(f"❌ Erro ao listar {caminho_repo}: {res.status_code}")
    return []

def importar_jogos_remoto(pasta_repo):
    print(f"📡 Buscando lista de jogos no GitHub...")
    arquivos = listar_arquivos_github(pasta_repo)
    conn = conectar()
    total = 0

    with conn.cursor() as cur:
        for nome_arq in arquivos:
            print(f"  -> Baixando jogo: {nome_arq}")
            res = requests.get(f"{RAW_URL_BASE}/{pasta_repo}/{nome_arq}")
            if res.status_code != 200: continue
            
            dados = res.json()
            lista_jogos = dados.get('data', []) if isinstance(dados, dict) else dados
            
            for j in lista_jogos:
                cur.execute("""
                    INSERT INTO jogos (
                        fixture_id, participant1_id, participant1_name, participant1_short_name, participant1_abbr,
                        participant2_id, participant2_name, participant2_short_name, participant2_abbr,
                        sport_id, sport_name, tournament_id, tournament_name, tournament_slug,
                        category_name, category_slug, season_id, status_id, status_name,
                        has_odds, start_time, true_start_time, true_end_time, api_updated_at,
                        external_providers
                    ) VALUES (
                        %(fixtureId)s, %(participant1Id)s, %(participant1Name)s, %(participant1ShortName)s, %(participant1Abbr)s,
                        %(participant2Id)s, %(participant2Name)s, %(participant2ShortName)s, %(participant2Abbr)s,
                        %(sportId)s, %(sportName)s, %(tournamentId)s, %(tournamentName)s, %(tournamentSlug)s,
                        %(categoryName)s, %(categorySlug)s, %(seasonId)s, %(statusId)s, %(statusName)s,
                        %(hasOdds)s, %(startTime)s, %(trueStartTime)s, %(trueEndTime)s, %(updatedAt)s,
                        %(externalProviders)s
                    ) ON CONFLICT (fixture_id) DO NOTHING;
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
                total += 1
            conn.commit()
            time.sleep(0.5) # Evitar block da API do GitHub

    conn.close()
    print(f"✅ {total} jogos processados.")

def importar_odds_remoto(pasta_repo):
    print(f"📡 Buscando lista de odds no GitHub...")
    arquivos = listar_arquivos_github(pasta_repo)
    conn = conectar()
    total_salvo = 0

    for nome_arq in arquivos:
        f_id = nome_arq.replace(".json", "")
        print(f"  -> Baixando odd: {f_id}")
        res = requests.get(f"{RAW_URL_BASE}/{pasta_repo}/{nome_arq}")
        if res.status_code != 200: continue
        
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO jogos_odds (fixture_id, odds_brutas) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (f_id, Json(res.json()))
                )
                conn.commit()
                total_salvo += 1
            except Exception as e:
                conn.rollback()
                print(f"    ⚠️ Erro ao salvar {f_id}: {e}")
        
        time.sleep(0.3)

    conn.close()
    print(f"✅ {total_salvo} odds importadas.")

if __name__ == "__main__":
    importar_jogos_remoto("jogos/odds-baixadas")
    #importar_odds_remoto("odds/april/c")