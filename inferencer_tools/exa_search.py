def _exa_search(query, num_results=5, category=None):
    import os
    import json
    import requests

    api_key = os.environ.get("EXA_API_KEY") or "97dbd594-f7b4-4866-9a8e-6a297e3df576"

    try:
        body = {
            'query': query,
            'type': 'auto',
            'num_results': num_results,
            'contents': {
                'highlights': {'max_characters': 4000}
            },
        }
        if category:
            body['category'] = category

        headers = {
            'x-api-key': api_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
        }

        response = requests.post(
            'https://api.exa.ai/search',
            json=body,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        response_data = response.json()

        results = []
        for r in response_data.get('results', []):
            snippet = ''
            highlights = r.get('highlights', [])
            if highlights:
                snippet = ' '.join(highlights)

            results.append({
                'title': r.get('title', ''),
                'link': r.get('url', ''),
                'snippet': snippet,
            })

        search_info = {
            'query': query,
            'results': results,
            'result_count': len(results),
        }

        if category:
            search_info['category'] = category

        if not results:
            search_info['message'] = 'No search results found. Try a different query.'

        return json.dumps(search_info, indent=1)

    except requests.exceptions.HTTPError as e:
        error_body = ''
        try:
            error_body = e.response.text
        except Exception:
            pass
        return json.dumps({
            'query': query,
            'results': [],
            'error': f'exa_search HTTP {e.response.status_code}: {error_body}'
        })

    except Exception as e:
        return json.dumps({
            'query': query,
            'results': [],
            'error': f'exa_search failed: {str(e)}'
        })
