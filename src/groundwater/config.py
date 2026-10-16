from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class Config:
    site: str = "USGS-444302070252401"
    start: str = "2023-01-01"
    end: str = "2024-12-31"
    parameter_code: str = "72019"
    statistic_id: str = "00003"
    approved_only: bool = False
    horizon: int = 14
    folds: int = 4
    min_train_days: int = 365
    max_gap_days: int = 3
    min_coverage: float = 0.95
    anomaly_window: int = 60
    anomaly_threshold: float = 4.5
    anomaly_scale_floor_ft: float = 0.05

    def __post_init__(self):
        if date.fromisoformat(self.start) > date.fromisoformat(self.end):
            raise ValueError("start must precede end")
        if not self.site.startswith("USGS-"):
            raise ValueError("Use the full USGS- monitoring location identifier")
        if (self.parameter_code, self.statistic_id) != ("72019", "00003"):
            raise ValueError("Starter supports only 72019 / 00003 (daily mean depth in ft)")
        if self.horizon < 1 or self.folds < 2 or self.min_train_days < 365:
            raise ValueError("Need horizon >= 1, folds >= 2, min_train_days >= 365")
        if self.max_gap_days < 0 or not 0 < self.min_coverage <= 1:
            raise ValueError("Invalid gap or coverage configuration")
        if self.anomaly_window < 10 or self.anomaly_threshold <= 0:
            raise ValueError("Invalid anomaly window or threshold")
        if self.anomaly_scale_floor_ft <= 0:
            raise ValueError("Anomaly scale floor must be positive")

    @classmethod
    def load(cls, path: str | Path):
        with open(path, "rb") as handle:
            return cls(**tomllib.load(handle))

    def as_dict(self):
        return asdict(self)
