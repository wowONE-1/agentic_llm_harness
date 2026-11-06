def check(read):
    import json
    obj = json.loads(read("config.json"))
    return obj == {"host":"localhost","port":8080,"debug":False} and type(obj["port"]) is int and type(obj["debug"]) is bool
