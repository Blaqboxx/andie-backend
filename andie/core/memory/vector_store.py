
import json
import os
import urllib.request

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

class VectorStore:
	def __init__(self, model_name='all-MiniLM-L6-v2', persist_path=None):
		self.model = None
		self.dimension = None
		self.index = None
		self.enabled = False
		self.disabled_reason = None
		self.provider = "none"
		self.ollama_endpoint = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
		self.ollama_model = os.environ.get("ANDIE_EMBED_OLLAMA_MODEL", "nomic-embed-text")
		self.ollama_timeout_seconds = float(os.environ.get("ANDIE_EMBED_OLLAMA_TIMEOUT", "90"))

		allow_remote = os.environ.get("ANDIE_EMBED_ALLOW_REMOTE", "0").strip().lower() in ("1", "true", "yes", "on")
		model_kwargs = {}
		if not allow_remote:
			# Default to local-only model loads so startup does not block on external DNS/network.
			model_kwargs["local_files_only"] = True

		try:
			self.model = SentenceTransformer(model_name, **model_kwargs)
			self.dimension = self.model.get_sentence_embedding_dimension()
			self.index = faiss.IndexFlatL2(self.dimension)
			self.enabled = True
			self.provider = "sentence-transformers"
		except Exception as e:
			# Fallback to Ollama local embeddings for offline/local runtime cognition.
			try:
				self.ollama_model = self._resolve_ollama_model(self.ollama_model)
				self.enabled = True
				self.provider = "ollama"
				self.disabled_reason = None
				print(f"[ANDIE] Embeddings provider fallback: ollama ({self.ollama_model})")
			except Exception as ollama_err:
				self.disabled_reason = str(ollama_err)
				print(f"[ANDIE] Embeddings disabled: sentence-transformers={e}; ollama={ollama_err}")

		self.texts = []  # stores raw memory
		self.metadata = []  # optional (ids, timestamps)
		self.persist_path = persist_path
		if persist_path:
			self._load()

	def _resolve_ollama_model(self, preferred_model):
		with urllib.request.urlopen(f"{self.ollama_endpoint}/api/tags", timeout=5) as resp:
			payload = json.loads(resp.read().decode("utf-8"))
		models = payload.get("models", []) if isinstance(payload, dict) else []
		names = [m.get("name") for m in models if isinstance(m, dict) and m.get("name")]
		if preferred_model in names:
			return preferred_model
		if names:
			return names[0]
		raise RuntimeError("No Ollama models available for embedding fallback")

	def _embed_ollama(self, texts):
		vectors = []
		for text in texts:
			attempts = [
				("/api/embeddings", {"model": self.ollama_model, "prompt": text}),
				("/api/embed", {"model": self.ollama_model, "input": text}),
			]
			last_error = None
			for path, payload_obj in attempts:
				payload = json.dumps(payload_obj).encode("utf-8")
				req = urllib.request.Request(
					url=f"{self.ollama_endpoint}{path}",
					data=payload,
					headers={"Content-Type": "application/json"},
					method="POST",
				)
				try:
					with urllib.request.urlopen(req, timeout=self.ollama_timeout_seconds) as resp:
						body = json.loads(resp.read().decode("utf-8"))
						emb = body.get("embedding")
						if emb is None and isinstance(body.get("embeddings"), list) and body.get("embeddings"):
							emb = body["embeddings"][0]
						if emb is None:
							raise RuntimeError("Ollama response missing embedding vector")
						vectors.append(emb)
						last_error = None
						break
				except Exception as e:
					last_error = e
			if last_error is not None:
				raise last_error
		return np.asarray(vectors, dtype="float32")

	def _embed(self, texts):
		if self.provider == "sentence-transformers" and self.model is not None:
			return np.asarray(self.model.encode(texts), dtype="float32")
		if self.provider == "ollama":
			return self._embed_ollama(texts)
		raise RuntimeError("No embedding provider configured")

	def add(self, text, meta=None):
		if not self.enabled:
			return
		try:
			embedding = self._embed([text])
		except Exception as e:
			msg = str(e).strip().lower()
			if (not msg) or ("timed out" in msg):
				# First embedding call can be slow while model warms; keep provider enabled.
				print("[ANDIE] Embeddings add timed out during warmup; keeping provider enabled")
				return
			self.enabled = False
			self.disabled_reason = str(e)
			print(f"[ANDIE] Embeddings provider disabled during add: {e}")
			return
		if self.index is not None and embedding.shape[1] != self.index.d:
			# Persisted index may be from a different model/provider dimension.
			print(f"[ANDIE] Vector index dimension changed ({self.index.d} -> {embedding.shape[1]}), rebuilding index")
			self.index = None
			self.texts = []
			self.metadata = []
		if self.index is None:
			self.dimension = embedding.shape[1]
			self.index = faiss.IndexFlatL2(self.dimension)
		try:
			self.index.add(embedding)
		except Exception as e:
			# Recover from stale/corrupt persisted indexes by rebuilding once.
			print(f"[ANDIE] Vector index add failed ({e}); rebuilding index")
			self.dimension = embedding.shape[1]
			self.index = faiss.IndexFlatL2(self.dimension)
			self.texts = []
			self.metadata = []
			try:
				self.index.add(embedding)
			except Exception as inner:
				self.enabled = False
				self.disabled_reason = str(inner)
				print(f"[ANDIE] Embeddings provider disabled after index rebuild failure: {inner}")
				return
		self.texts.append(text)
		self.metadata.append(meta)
		if self.persist_path:
			self._save()

	def search(self, query, k=5):
		if not self.enabled or self.index is None:
			return []
		try:
			embedding = self._embed([query])
		except Exception as e:
			msg = str(e).strip().lower()
			if (not msg) or ("timed out" in msg):
				print("[ANDIE] Embeddings search timed out; keeping provider enabled")
				return []
			self.enabled = False
			self.disabled_reason = str(e)
			print(f"[ANDIE] Embeddings provider disabled during search: {e}")
			return []
		D, I = self.index.search(embedding, k)
		results = []
		for i in I[0]:
			if i < len(self.texts):
				results.append({
					"text": self.texts[i],
					"meta": self.metadata[i]
				})
		return results

	def _save(self):
		import pickle
		if self.index is None:
			return
		faiss.write_index(self.index, self.persist_path + '.index')
		with open(self.persist_path + '.pkl', 'wb') as f:
			pickle.dump({'texts': self.texts, 'metadata': self.metadata}, f)

	def _load(self):
		import os, pickle
		if os.path.exists(self.persist_path + '.index') and os.path.exists(self.persist_path + '.pkl'):
			self.index = faiss.read_index(self.persist_path + '.index')
			self.dimension = self.index.d
			with open(self.persist_path + '.pkl', 'rb') as f:
				data = pickle.load(f)
				self.texts = data.get('texts', [])
				self.metadata = data.get('metadata', [])
