TV_UA = "Mozilla/5.0 (SmartTV; Linux; Tizen 7.0) AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36"


def post(client, path, body=None):
    return client.post(path, json=body or {})
