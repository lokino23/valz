import copy, os, yaml
_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_DIR, "config.example.yaml"), encoding="utf-8") as f:
    DEFAULTS = yaml.safe_load(f)

def load_config(path=None):
    cfg = copy.deepcopy(DEFAULTS)
    if path:
        with open(path, encoding="utf-8") as f:
            cfg.update(yaml.safe_load(f) or {})
    return cfg
