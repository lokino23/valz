import os, yaml
_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULTS = yaml.safe_load(open(os.path.join(_DIR, "config.example.yaml"), encoding="utf-8"))

def load_config(path=None):
    cfg = dict(DEFAULTS)
    if path:
        cfg.update(yaml.safe_load(open(path, encoding="utf-8")) or {})
    return cfg
