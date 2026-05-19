
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer
import time
import math

class VectorStore:
	def __init__(self, model_name='all-MiniLM-L6-v2', persist_path=None):
		# self.model = SentenceTransformer(model_name)
		# self.dimension = self.model.get_sentence_embedding_dimension()
		# self.index = faiss.IndexFlatL2(self.dimension)
		self.texts = []  # stores raw memory
		self.metadata = []  # stores dicts with scoring fields
		self.persist_path = persist_path
		# if persist_path:
		#     self._load()

	def add(self, text, meta=None):
		embedding = self.model.encode([text])
		self.index.add(np.array(embedding).astype('float32'))
		now = time.time()
		meta = meta or {}
		meta.setdefault("timestamp", now)
		meta.setdefault("last_accessed", now)
		meta.setdefault("access_count", 0)
		meta.setdefault("confidence", 0.5)
		self.texts.append(text)
		self.metadata.append(meta)
		if self.persist_path:
			self._save()

	def update_meta(self, idx, **kwargs):
		for k, v in kwargs.items():
			self.metadata[idx][k] = v
		if self.persist_path:
			self._save()

	def search(self, query, k=5, filter_tags=None, return_scores=True):
		if not self.texts:
			return []
		embedding = self.model.encode([query])
		D, I = self.index.search(np.array(embedding).astype('float32'), min(k*2, len(self.texts)))
		results = []
		for rank, (dist, i) in enumerate(zip(D[0], I[0])):
			if i < len(self.texts):
				meta = self.metadata[i]
				# Hybrid: filter by tags if provided
				if filter_tags:
					tags = meta.get("tags", [])
					if not any(tag in query.lower() for tag in tags):
						continue
				# Update access count and last_accessed
				meta["access_count"] = meta.get("access_count", 0) + 1
				meta["last_accessed"] = time.time()
				# Compute similarity (convert L2 to similarity)
				similarity = 1 / (1 + dist)
				result = {
					"text": self.texts[i],
					"meta": meta,
					"similarity": similarity
				} if return_scores else {
					"text": self.texts[i],
					"meta": meta
				}
				results.append(result)
				if len(results) >= k:
					break
		return results

	def reinforce(self, idx, positive=True, confidence=None):
		meta = self.metadata[idx]
		if positive:
			meta["feedback"] = "positive"
			meta["confidence"] = max(meta.get("confidence", 0.5), confidence or 0.8)
		else:
			meta["feedback"] = "negative"
			meta["confidence"] = min(meta.get("confidence", 0.5), confidence or 0.2)
		if self.persist_path:
			self._save()

	def compute_weight(self, meta, similarity):
		now = time.time()
		age_days = (now - meta.get("timestamp", now)) / 86400
		last_used_days = (now - meta.get("last_accessed", now)) / 86400
		decay = math.exp(-age_days / 7)  # 7-day half-life
		recency_boost = math.exp(-last_used_days / 3)
		feedback_boost = 1.2 if meta.get("feedback") == "positive" else 0.7
		usage_boost = 1 + (meta.get("access_count", 0) * 0.05)
		confidence = meta.get("confidence", 0.5)
		return similarity * decay * recency_boost * feedback_boost * usage_boost * confidence

	def _save(self):
		import pickle
		faiss.write_index(self.index, self.persist_path + '.index')
		with open(self.persist_path + '.pkl', 'wb') as f:
			pickle.dump({'texts': self.texts, 'metadata': self.metadata}, f)

	def _load(self):
		import os, pickle
		if os.path.exists(self.persist_path + '.index') and os.path.exists(self.persist_path + '.pkl'):
			self.index = faiss.read_index(self.persist_path + '.index')
			with open(self.persist_path + '.pkl', 'rb') as f:
				data = pickle.load(f)
				self.texts = data.get('texts', [])
				self.metadata = data.get('metadata', [])