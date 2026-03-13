+++
name = "maps"
description = "Busca locais, calcula rotas, distâncias e tempo de deslocamento via Google Maps APIs ou OpenStreetMap/Nominatim (gratuito). Use when: user asks to encontrar restaurante, hotel, ponto turístico, calcular rota, distância entre dois pontos, tempo de viagem de carro/transporte/a pé. NOT for: reservas (use travel skill), voos (use travel skill), trânsito em tempo real sem key."
tags = ["mapa", "rota", "direção", "endereço", "localização", "distância", "google maps", "como chegar", "restaurante", "ponto turístico", "geolocalizao", "maps"]
priority = "contextual"

[sif]
signature = "maps.route(origin: str, destination: str, mode: str = 'driving') -> dict"
prompt_hint = "Use para achar lugares, calcular rota, distância ou tempo de deslocamento entre pontos."
short_sig = "maps.route(orig,dest,mode)"
compose = ["travel", "calendar", "email"]
examples_min = ["calcular rota e tempo entre dois endereços"]

[runtime]
estimated_cost = 0.6
risk_level = "low"
side_effects = ["http_request", "geo_lookup"]
postconditions = ["route_or_place_identified"]
fallback_policy = "use_web_search_for_place_disambiguation"

[quality]
historical_reliability = 0.5
success_count = 0
failure_count = 0
last_30d_utility = 0.5

[retrieval]
embedding_text = "maps route directions distance geocode address travel restaurant hotel place"
example_queries = ["como chegar de A para B", "encontre restaurantes perto daqui"]

[requires]
bins = []
+++

# Maps Skill

Geolocalização, busca de locais e rotas via Google Maps API ou OSM/Nominatim (sem key).

## Quando usar

✅ **USE quando:**
- "Encontra restaurantes italianos perto do centro do Rio"
- "Quanto tempo leva de São Paulo para Campinas de carro?"
- "Coordenadas de Machu Picchu"
- "Hotéis 4 estrelas em Buenos Aires"
- "Rota de bicicleta entre dois endereços"

❌ **NÃO use quando:**
- Reservar hotel → use `travel` skill
- Comprar passagem aérea → use `travel` skill
- Navegação GPS em tempo real → requer app nativo

## OpenStreetMap / Nominatim (GRÁTIS — sem API key)

```python
import requests

def geocode(address: str) -> dict:
    """Converte endereço em coordenadas."""
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": address, "format": "json", "limit": 1},
        headers={"User-Agent": "RLM-Agent/1.0"},
        timeout=10,
    )
    results = r.json()
    if not results:
        return {}
    loc = results[0]
    return {
        "lat": float(loc["lat"]),
        "lon": float(loc["lon"]),
        "display_name": loc["display_name"],
    }

coords = geocode("Torre Eiffel, Paris")
print(coords)  # {"lat": 48.858..., "lon": 2.294..., "display_name": "..."}
```

## Buscar locais próximos (Overpass API — OSM, grátis)

```python
import requests, json

def buscar_perto(lat: float, lon: float, tipo: str, raio_m: int = 1000) -> list[dict]:
    """
    tipo: restaurant | hotel | museum | hospital | supermarket | cafe | ...
    Retorna lista de locais com nome, lat, lon.
    """
    query = f"""
    [out:json][timeout:15];
    node["amenity"="{tipo}"](around:{raio_m},{lat},{lon});
    out body;
    """
    r = requests.post(
        "https://overpass-api.de/api/interpreter",
        data={"data": query},
        timeout=20,
    )
    elements = r.json().get("elements", [])
    return [
        {
            "name": e.get("tags", {}).get("name", "Sem nome"),
            "lat": e["lat"],
            "lon": e["lon"],
            "tags": e.get("tags", {}),
        }
        for e in elements
        if "lat" in e
    ][:20]

# Restaurantes próximos ao centro de SP
coords_sp = geocode("Praça da Sé, São Paulo")
restaurantes = buscar_perto(coords_sp["lat"], coords_sp["lon"], "restaurant", raio_m=500)
for r in restaurantes[:5]:
    print(f"{r['name']} — {r['lat']:.5f}, {r['lon']:.5f}")
```

## Calcular distância e rota (OSRM — grátis, sem key)

```python
import requests

def rota_carro(origem_lat: float, origem_lon: float,
               destino_lat: float, destino_lon: float) -> dict:
    """
    Calcula rota via OSRM (Open Source Routing Machine) — gratuito.
    Retorna distância em km e duração estimada em minutos.
    """
    coords = f"{origem_lon},{origem_lat};{destino_lon},{destino_lat}"
    r = requests.get(
        f"https://router.project-osrm.org/route/v1/driving/{coords}",
        params={"overview": "false"},
        timeout=15,
    )
    data = r.json()
    if data.get("code") != "Ok":
        return {"error": data.get("code")}
    route = data["routes"][0]
    return {
        "distancia_km": round(route["distance"] / 1000, 1),
        "duracao_min": round(route["duration"] / 60, 0),
        "duracao_h": round(route["duration"] / 3600, 2),
    }

sp = geocode("Praça da Sé, São Paulo, Brazil")
campinas = geocode("Praça Carlos Gomes, Campinas, Brazil")
rota = rota_carro(sp["lat"], sp["lon"], campinas["lat"], campinas["lon"])
print(f"SP → Campinas: {rota['distancia_km']} km, ~{rota['duracao_min']:.0f} minutos")
```

## Google Maps API (com chave — mais preciso)

```python
import requests, os

GMAPS_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")

def gmaps_places(query: str, location: str = "", radius_m: int = 5000) -> list[dict]:
    """Text search de locais via Google Places API."""
    params = {
        "query": query,
        "key": GMAPS_KEY,
    }
    if location:
        coord = geocode(location)
        if coord:
            params["location"] = f"{coord['lat']},{coord['lon']}"
            params["radius"] = radius_m

    r = requests.get(
        "https://maps.googleapis.com/maps/api/place/textsearch/json",
        params=params,
        timeout=15,
    )
    results = r.json().get("results", [])
    return [
        {
            "name": p["name"],
            "address": p.get("formatted_address", ""),
            "rating": p.get("rating", 0),
            "user_ratings_total": p.get("user_ratings_total", 0),
            "lat": p["geometry"]["location"]["lat"],
            "lon": p["geometry"]["location"]["lng"],
            "place_id": p["place_id"],
            "types": p.get("types", []),
        }
        for p in results[:10]
    ]

hoteis = gmaps_places("hotel 4 estrelas Buenos Aires")
for h in hoteis[:5]:
    print(f"{h['name']} — ⭐{h['rating']} ({h['user_ratings_total']} avaliações) — {h['address']}")
```

## Google Directions API (rota multimodal)

```python
import requests, os

GMAPS_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")

def rota_google(origem: str, destino: str, modo: str = "driving") -> dict:
    """
    modo: driving | walking | bicycling | transit
    """
    r = requests.get(
        "https://maps.googleapis.com/maps/api/directions/json",
        params={
            "origin": origem,
            "destination": destino,
            "mode": modo,
            "language": "pt-BR",
            "key": GMAPS_KEY,
        },
        timeout=15,
    )
    data = r.json()
    if data.get("status") != "OK":
        return {"error": data.get("status")}

    leg = data["routes"][0]["legs"][0]
    return {
        "distancia": leg["distance"]["text"],
        "duracao": leg["duration"]["text"],
        "origem": leg["start_address"],
        "destino": leg["end_address"],
        "passos": [s["html_instructions"].replace("<b>", "").replace("</b>", "") for s in leg["steps"][:10]],
    }

rota = rota_google("Av. Paulista, São Paulo", "Aeroporto de Congonhas", modo="driving")
print(f"{rota['distancia']} — {rota['duracao']}")
for passo in rota["passos"]:
    print(f"  • {passo}")
```

## Variáveis de ambiente

| Variável | Descrição |
|---|---|
| `GOOGLE_MAPS_API_KEY` | Chave Google Maps (Places, Directions, Geocoding) |

**Sem key:** use Nominatim (geocode) + Overpass (busca) + OSRM (rotas). Totalmente gratuito com limite de uso razoável.
