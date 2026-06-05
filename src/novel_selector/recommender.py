from __future__ import annotations

from .db import Database
from .llm import LLMClient
from .models import Recommendation


class RecommendationService:
    def __init__(self, db: Database, llm: LLMClient):
        self.db = db
        self.llm = llm

    def recommend(self, k: int, pool_size: int = 30) -> tuple[int | None, list[Recommendation]]:
        profile = self.db.preference_profile()
        rows = self.db.recommendable_samples(pool_size)
        recs = self.llm.score_candidates(profile, rows, k)
        if not recs:
            return None, []
        run_id = self.db.save_recommendations(k, self.llm.settings.openai_model, profile, recs)
        return run_id, recs

