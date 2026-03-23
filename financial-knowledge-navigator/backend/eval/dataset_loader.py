import json
from pathlib import Path
from typing import List, Dict


def load_golden_dataset(path: str = "data/golden_set/ground_truth_qa.json") -> List[Dict]:
    dataset_path = Path(path)
    if not dataset_path.exists():
        raise FileNotFoundError(f"Golden dataset not found at: {path}")

    with open(dataset_path, "r", encoding="utf-8") as f:
        return json.load(f)
