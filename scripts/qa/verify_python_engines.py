import importlib.util
import os
import sys


def _load_module(module_name, rel_path):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    full_path = os.path.join(root, rel_path)
    module_dir = os.path.dirname(full_path)

    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)

    spec = importlib.util.spec_from_file_location(module_name, full_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_inputs():
    H = {
        "Home": {
            "GP": 20,
            "OFF": 120,
            "DEF": 80,
            "TS": 0.60,
            "TO": 0.10,
            "ORR": 0.30,
            "PACE": 100,
        },
        "Away": {
            "GP": 20,
            "OFF": 118,
            "DEF": 82,
            "TS": 0.58,
            "TO": 0.11,
            "ORR": 0.28,
            "PACE": 98,
        },
    }

    g = {
        "home": "Home",
        "away": "Away",
        "line": 5,
        "total": 150,
        "_date": "20240201",
    }

    kalman = {
        "teams": {
            "Home": {"adj_mean": 0.0, "adj_var": 1.0},
            "Away": {"adj_mean": 0.0, "adj_var": 1.0},
        }
    }

    h2h = {
        "Away::Home": {
            "games": [
                {
                    "date": "20240101",
                    "home": {"team": "Home", "pts": 120},
                    "away": {"team": "Away", "pts": 100},
                }
            ]
        }
    }

    return H, g, kalman, h2h


def test_python_engines():
    nba = _load_module("py_nba_engine", os.path.join("PythonNBA", "scripts", "model_engine.py"))
    full = _load_module("py_full_engine", os.path.join("PythonFull", "scripts", "model_engine.py"))
    ncaa = _load_module("py_ncaa_engine", os.path.join("PythonNCAA", "scripts", "model_engine.py"))

    H, g, kalman, h2h = _build_inputs()

    avgs_nba = nba.get_avgs(H)
    W_nba = nba.load_defaults()["DEFAULT_W"]
    W_var_nba = nba.load_defaults()["DEFAULT_W_VAR"]

    avgs_full = full.get_avgs(H)
    W_full = full.load_defaults()["DEFAULT_W"]
    W_var_full = full.load_defaults()["DEFAULT_W_VAR"]

    avgs_ncaa = ncaa.get_avgs(H)
    W_ncaa = ncaa.load_defaults()["DEFAULT_W"]
    W_var_ncaa = ncaa.load_defaults()["DEFAULT_W_VAR"]

    res_nba = nba.analyze_game(g, H, avgs_nba, W_nba, None, kalman, W_var_nba, 100)
    res_full = full.analyze_game(g, H, avgs_full, W_full, None, kalman, W_var_full, 100, h2h)
    res_ncaa = ncaa.analyze_game(g, H, avgs_ncaa, W_ncaa, None, kalman, W_var_ncaa, 130)

    assert res_nba is not None, "NBA analyze_game returned None"
    assert res_full is not None, "Fullseason analyze_game returned None"
    assert res_ncaa is not None, "NCAA analyze_game returned None"

    assert res_nba.get("oPick") == "OVER", "NBA should emit total pick"
    assert res_full.get("oPick") == "OVER", "Fullseason should emit total pick"
    assert res_ncaa.get("oPick") == "PASS", "NCAA totals should be disabled"

    assert "h2hAdj" not in res_nba, "NBA should not include H2H fields"
    assert res_full.get("h2hAdj", 0) != 0, "Fullseason should include H2H adjustment"

    print("Python engine QA OK")


if __name__ == "__main__":
    test_python_engines()
