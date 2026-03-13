+++
name = "travel"
description = "Planeja viagens completas: busca voos via Amadeus/Skyscanner, hotéis via Booking.com/Expedia APIs, e gera roteiros detalhados combinando maps+web_search. Use when: user asks to planejar viagem, buscar voos, pesquisar hotéis, criar roteiro de cidade, calcular custo de viagem, buscar vistos/documentos necessários. NOT for: táxi/ride-share (use maps), reserva de restaurante apenas (use maps)."
tags = ["viagem", "passagem", "hotel", "voo", "roteiro", "amadeus", "booking", "viajar", "hospedagem", "skyscanner", "travel", "turismo", "visto"]
priority = "contextual"

[requires]
bins = []

[sif]
signature = "travel.plan(origin: str, dest: str, date: str, nights: int = 3) -> dict"
prompt_hint = "Use para montar viagem completa com voos, hotel, roteiro, custo e logística entre locais."
short_sig = "travel.plan(o,d,dt,n=3)→{}"
compose = ["maps", "web_search", "browser", "calendar"]
examples_min = ["planejar viagem com voo, hotel e roteiro básico"]

[runtime]
estimated_cost = 1.1
risk_level = "medium"
side_effects = ["http_request", "travel_search"]
postconditions = ["travel_options_or_itinerary_generated"]
fallback_policy = "fallback_to_maps_and_web_search"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "travel flights hotels itinerary trip planning visa budget tourism"
example_queries = ["planeje uma viagem para Lisboa", "busque voos e hotéis para Tóquio"]
+++

# Travel Skill

Planejamento completo de viagens: voos, hotéis, roteiros e logística.

## Quando usar

✅ **USE quando:**
- "Planeja uma viagem de 5 dias em Tóquio"
- "Qual o voo mais barato de SP para Lisboa essa semana?"
- "Hotéis 3 estrelas em Amsterdam em março"
- "Escreve um roteiro detalhado para Buenos Aires"
- "Docs necessários para viajar ao Japão como brasileiro"
- "Quanto custa uma semana em Cancún?"

## Buscar voos — Amadeus (FREE tier: 2000 req/mês)

```python
import requests, os

AMADEUS_KEY    = os.environ.get("AMADEUS_API_KEY", "")
AMADEUS_SECRET = os.environ.get("AMADEUS_API_SECRET", "")

def amadeus_token() -> str:
    """Obtém access token OAuth2 do Amadeus."""
    r = requests.post(
        "https://test.api.amadeus.com/v1/security/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": AMADEUS_KEY,
            "client_secret": AMADEUS_SECRET,
        },
        timeout=10,
    )
    return r.json()["access_token"]

def buscar_voos(
    origem: str,      # IATA: "GRU"
    destino: str,     # IATA: "LIS"
    data_ida: str,    # "2026-04-15"
    data_volta: str | None = None,
    adultos: int = 1,
    max_resultados: int = 5,
) -> list[dict]:
    token = amadeus_token()
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "originLocationCode": origem,
        "destinationLocationCode": destino,
        "departureDate": data_ida,
        "adults": adultos,
        "currencyCode": "BRL",
        "max": max_resultados,
    }
    if data_volta:
        params["returnDate"] = data_volta

    r = requests.get(
        "https://test.api.amadeus.com/v2/shopping/flight-offers",
        headers=headers,
        params=params,
        timeout=20,
    )
    data = r.json()
    results = []
    for offer in data.get("data", []):
        price = offer["price"]["total"]
        segments = offer["itineraries"][0]["segments"]
        primeiro = segments[0]
        results.append({
            "preco_brl": float(price),
            "partida": primeiro["departure"]["at"],
            "chegada": segments[-1]["arrival"]["at"],
            "companhia": primeiro["carrierCode"],
            "paradas": len(segments) - 1,
            "duracao": offer["itineraries"][0]["duration"],
        })
    return results

voos = buscar_voos("GRU", "LIS", "2026-05-01", data_volta="2026-05-15")
for v in voos:
    print(f"R$ {v['preco_brl']:.2f} — {v['partida']} → {v['chegada']} ({v['paradas']} paradas, {v['duracao']})")
```

## Buscar código IATA de aeroporto

```python
def iata_lookup(cidade: str) -> list[dict]:
    token = amadeus_token()
    r = requests.get(
        "https://test.api.amadeus.com/v1/reference-data/locations",
        headers={"Authorization": f"Bearer {token}"},
        params={"keyword": cidade, "subType": "AIRPORT,CITY"},
        timeout=10,
    )
    return [
        {"iata": loc["iataCode"], "nome": loc["name"], "tipo": loc["subType"]}
        for loc in r.json().get("data", [])[:5]
    ]

print(iata_lookup("São Paulo"))  # [{'iata': 'GRU', 'nome': 'SAO PAULO...'}, ...]
```

## Buscar hotéis — Booking.com Affiliate API

```python
import requests, os

BOOKING_TOKEN = os.environ.get("BOOKING_API_TOKEN", "")

def buscar_hoteis(
    destino_id: int,      # dest_id do Booking.com
    checkin: str,         # "2026-05-01"
    checkout: str,        # "2026-05-07"
    adultos: int = 2,
    quartos: int = 1,
) -> list[dict]:
    headers = {
        "X-Rapidapi-Key": BOOKING_TOKEN,
        "X-Rapidapi-Host": "booking-com.p.rapidapi.com",
    }
    r = requests.get(
        "https://booking-com.p.rapidapi.com/v1/hotels/search",
        headers=headers,
        params={
            "dest_id": destino_id,
            "dest_type": "city",
            "checkin_date": checkin,
            "checkout_date": checkout,
            "adults_number": adultos,
            "room_number": quartos,
            "units": "metric",
            "currency": "BRL",
            "order_by": "price",
            "locale": "pt-br",
        },
        timeout=20,
    )
    results = r.json().get("result", [])
    return [
        {
            "nome": h.get("hotel_name", ""),
            "estrelas": h.get("class", 0),
            "nota": h.get("review_score", 0),
            "preco_noite_brl": h.get("min_total_price", 0),
            "endereco": h.get("address", ""),
            "url": h.get("url", ""),
        }
        for h in results[:10]
    ]
```

## Gerar roteiro completo com sub_rlm

```python
# Estratégia: sub_rlm para decomposição, web_search para pesquisa, maps para logística

def gerar_roteiro_completo(cidade: str, dias: int, perfil: str = "turista cultura") -> str:
    """
    perfil: "turista cultura" | "aventura" | "família" | "gastronomia" | "negócios"
    """
    # Sub-tarefa 1: pesquisa dos principais pontos
    pontos = sub_rlm(
        f"Lista os {dias * 3} principais pontos turísticos de {cidade} "
        f"para perfil '{perfil}'. Para cada um: nome, bairro, tempo recomendado, preço entrada."
        f"Use web_search para informações atualizadas."
    )

    # Sub-tarefa 2: logística (distâncias e agrupamento por bairro)
    logistica = sub_rlm(
        f"Dado os pontos a seguir de {cidade}, agrupa-os por bairro e proximidade "
        f"para minimizar deslocamento. Sugere sequência de visita e meios de transporte.\n\n{pontos}"
    )

    # Sub-tarefa 3: dicas práticas
    info_pratica = sub_rlm(
        f"Para viagem a {cidade} ({dias} dias, perfil {perfil}), fornece: "
        f"melhor época, moeda, transporte local, gastronomia obrigatória, "
        f"custo médio diário estimado em BRL (câmbio atual via web_search)."
    )

    roteiro = sub_rlm(
        f"Monte um roteiro dia-a-dia de {dias} dias em {cidade} integrando:\n\n"
        f"PONTOS E DURAÇÃO:\n{pontos}\n\n"
        f"LOGÍSTICA:\n{logistica}\n\n"
        f"INFO PRÁTICA:\n{info_pratica}\n\n"
        f"Formato: tabela por dia com horário, local, duração, custo estimado, dica pessoal."
    )
    return roteiro

# Uso direto no REPL:
roteiro = gerar_roteiro_completo("Kyoto, Japão", dias=5, perfil="cultura")
FINAL_VAR("roteiro")
```

## Informações de visto / documentação

```python
def info_visto(nacionalidade: str, destino: str) -> str:
    """Busca requisitos de entrada via web_search."""
    query = f"visto {destino} para {nacionalidade} 2026 requisitos documentos"
    resultados = web_search(query, max_results=3)
    
    contexto = "\n\n".join([f"[{r['title']}]({r['url']})\n{r['snippet']}" for r in resultados])
    return sub_rlm(
        f"Com base nas informações abaixo, resume os requisitos de entrada em {destino} "
        f"para cidadão {nacionalidade}: visto necessário? gratuito? como solicitar? documentos?\n\n{contexto}"
    )

docs = info_visto("brasileiro", "Japão")
print(docs)
```

## Variáveis de ambiente

| Variável | Descrição |
|---|---|
| `AMADEUS_API_KEY` | Chave pública Amadeus (test: gratuito em [developers.amadeus.com](https://developers.amadeus.com)) |
| `AMADEUS_API_SECRET` | Segredo Amadeus |
| `BOOKING_API_TOKEN` | Token Booking.com via RapidAPI |
| `GOOGLE_MAPS_API_KEY` | Para roteiros com Maps (opcional) |
