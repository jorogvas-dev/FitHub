"""FitHub-only public recipe retrieval using deterministic local lexical vectors."""
import hashlib
import math
import os
import re
import httpx

COLLECTION = 'fithub_recipes_v1'
BASE_PATH = '/api/v2/tenants/default_tenant/databases/default_database/collections'
METADATA = {'owner':'fithub','embedding':'sha256-lexical-v1','dimension':256}


def embedding(text):
    words = re.findall(r'[a-z0-9]+',text.casefold())
    features = words+[' '.join(words[i:i+2]) for i in range(len(words)-1)]
    vector = [0.0]*256
    for feature in features:
        digest = hashlib.sha256(feature.encode()).digest()
        vector[int.from_bytes(digest[:2],'big')%256] += 1 if digest[2]%2 else -1
    norm = math.sqrt(sum(value*value for value in vector)) or 1
    return [value/norm for value in vector]


def document(row):
    # Explicit allowlist: shared public catalogue fields only; never settings/users.
    return row['name']+'\n'+row['description']+'\n'+' '.join(row['tags'])+'\n'+' '.join(item['food']['name'] for item in row['ingredients'])


def _collection(client, create=False):
    if create:
        response = client.post(BASE_PATH,json=dict(name=COLLECTION,get_or_create=True,metadata=METADATA))
    else:response = client.get(BASE_PATH+'/'+COLLECTION)
    response.raise_for_status()
    collection = response.json()
    if not all(collection.get('metadata',{}).get(key)==value for key,value in METADATA.items()):
        raise ValueError('Recipe collection metadata does not match FitHub lexical index.')
    return BASE_PATH+'/'+collection['id']


def index_recipes(rows, retired_ids=()):
    with httpx.Client(base_url=os.environ.get('CHROMA_URL','http://chroma:8000'),timeout=10,trust_env=False) as client:
        path = _collection(client,create=True)
        documents = [document(row) for row in rows]
        response = client.post(path+'/upsert',json=dict(ids=[row['recipe_id'] for row in rows],
            embeddings=[embedding(text) for text in documents],documents=documents,
            metadatas=[dict(recipe_id=row['recipe_id'],source='unitools',commit=row['provenance']['commit']) for row in rows]))
        response.raise_for_status()
        if retired_ids:
            # Only manifest-listed retired source IDs, in this FitHub-owned
            # collection. PostgreSQL rows and historical snapshots are retained.
            response=client.post(path+'/delete',json=dict(ids=list(retired_ids)))
            response.raise_for_status()
        return len(rows)


def rank(eligible, text, limit=12, targets=None):
    if not eligible:return []
    from .nutrition_fit import candidate_score
    ranked=[]
    with httpx.Client(base_url=os.environ.get('CHROMA_URL','http://chroma:8000'),timeout=5,trust_env=False) as client:
        path = _collection(client)
        # Three references per slot, twelve overall. Each query can see every
        # eligible recipe in that slot, including newly imported source IDs.
        for meal in ('breakfast','lunch','dinner','snack'):
            available=[row for row in eligible if meal in row['meals']]
            if not available:continue
            by_id={row['recipe_id']:row for row in available}
            response=client.post(path+'/query',json=dict(query_embeddings=[embedding(text+' '+meal)],
                n_results=min(3,len(by_id)),where={'recipe_id':{'$in':list(by_id)}},include=['distances']))
            response.raise_for_status()
            returned=[by_id[key] for key in dict.fromkeys(response.json()['ids'][0]) if key in by_id]
            # Retain lexical relevance and target-fit variety. No model numbers
            # influence this ranking; all candidate scores use exact stored data.
            numeric=sorted(available,key=lambda row:(candidate_score(row,targets or {},meal),row['recipe_id'])) if targets else []
            options=(returned[:1]+numeric+returned+available) if numeric else returned+available
            seen=set()
            chosen=[]
            for row in options:
                if row['recipe_id'] not in seen:
                    seen.add(row['recipe_id']);chosen.append(row)
                if len(chosen)==3:break
            ranked.extend(chosen)
    return list({row['recipe_id']:row for row in ranked}.values())[:limit]
