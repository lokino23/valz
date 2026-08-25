import pathlib, sqlite3

def init_db(path):
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    schema = pathlib.Path(__file__).parent.joinpath("schema.sql").read_text(encoding="utf-8")
    con = sqlite3.connect(path)
    con.executescript(schema); con.commit(); con.close()

def connect(path, readonly=False):
    uri = f"file:{path}?mode=ro" if readonly else f"file:{path}?mode=rwc"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con
