import os, logging, httpx
from enum import Enum
from typing import Optional

logger = logging.getLogger("andie.cognition")

class TaskType(Enum):
    ROUTING="routing"; MEMORY="memory"; HEALTH="health"; SIMPLE="simple"
    ECHO="echo"; TITAN="titan"; PHANTOM="phantom"
    ORCHESTRATION="orchestration"; ANALYSIS="analysis"; SELF_BUILD="self_build"
    CREATIVE="creative"; SECURITY="security"; COMPLEX="complex"; GENERAL="general"

LOCAL_TASKS={TaskType.ROUTING,TaskType.MEMORY,TaskType.HEALTH,TaskType.SIMPLE,TaskType.ECHO,TaskType.TITAN,TaskType.PHANTOM}
CLOUD_TASKS={TaskType.ORCHESTRATION,TaskType.ANALYSIS,TaskType.SELF_BUILD,TaskType.CREATIVE,TaskType.SECURITY,TaskType.COMPLEX,TaskType.GENERAL}

class OllamaProvider:
    def __init__(self):
        self.host=os.environ.get("OLLAMA_BASE_URL","http://172.18.0.1:11434")
        self.model=os.environ.get("OLLAMA_MODEL","mistral")
    def generate(self,message,system=None):
        msgs=([{"role":"system","content":system}] if system else [])+[{"role":"user","content":message}]
        r=httpx.post(f"{self.host}/api/chat",json={"model":self.model,"messages":msgs,"stream":False},timeout=30.0)
        r.raise_for_status()
        return r.json()["message"]["content"]

class ClaudeProvider:
    def __init__(self):
        self.api_key=os.environ.get("ANTHROPIC_API_KEY","")
        self.model=os.environ.get("CLAUDE_MODEL","claude-sonnet-4-20250514")
    def generate(self,message,system=None):
        if not self.api_key: raise ValueError("ANTHROPIC_API_KEY not set")
        payload={"model":self.model,"max_tokens":1024,"messages":[{"role":"user","content":message}]}
        if system: payload["system"]=system
        r=httpx.post("https://api.anthropic.com/v1/messages",headers={"x-api-key":self.api_key,"anthropic-version":"2023-06-01","content-type":"application/json"},json=payload,timeout=60.0)
        r.raise_for_status()
        return "".join(b.get("text","") for b in r.json().get("content",[]) if b.get("type")=="text")

class OpenAIProvider:
    def __init__(self):
        self.api_key=os.environ.get("OPENAI_API_KEY","")
        self.model=os.environ.get("OPENAI_MODEL","gpt-4o-mini")
    def generate(self,message,system=None):
        if not self.api_key: raise ValueError("OPENAI_API_KEY not set")
        from openai import OpenAI
        msgs=([{"role":"system","content":system}] if system else [])+[{"role":"user","content":message}]
        return OpenAI(api_key=self.api_key).chat.completions.create(model=self.model,messages=msgs).choices[0].message.content

class CognitionProvider:
    def __init__(self):
        self.ollama=OllamaProvider(); self.claude=ClaudeProvider(); self.openai=OpenAIProvider()
    def generate(self,message,task_type=None,system=None,force_local=False,force_cloud=False):
        if task_type is None: task_type=TaskType.GENERAL
        if task_type==TaskType.SELF_BUILD:
            logger.info("[Cognition] SELF_BUILD -> Claude (no fallback)")
            return self.claude.generate(message,system)
        use_local=(task_type in LOCAL_TASKS) and not force_cloud
        if force_local: use_local=True
        if force_cloud: use_local=False
        if use_local:
            logger.info(f"[Cognition] {task_type.value} -> Ollama")
            try: return self.ollama.generate(message,system)
            except Exception as e:
                logger.warning(f"[Cognition] Ollama failed: {e}, escalating to Claude")
                try: return self.claude.generate(message,system)
                except Exception as e2: return self._fallback(message,system,task_type)
        else:
            logger.info(f"[Cognition] {task_type.value} -> Claude")
            try: return self.claude.generate(message,system)
            except Exception as e:
                logger.warning(f"[Cognition] Claude failed: {e}, falling back to OpenAI")
                return self._fallback(message,system,task_type)
    def _fallback(self,message,system,task_type):
        logger.warning(f"[Cognition] {task_type.value} -> OpenAI (emergency)")
        try: return self.openai.generate(message,system)
        except Exception as e:
            logger.error(f"[Cognition] All providers failed: {e}")
            return "[ANDIE OFFLINE] All cognition providers unavailable."
    def health_check(self):
        out={}
        for name,p,role in [("ollama",self.ollama,"primary_local"),("claude",self.claude,"primary_cloud"),("openai",self.openai,"emergency_fallback")]:
            try: p.generate("ping"); out[name]={"status":"online","role":role}
            except Exception as e: out[name]={"status":"offline","error":str(e),"role":role}
        return out

cognition=CognitionProvider()

def safe_call_llm(message,task_type=None):
    return cognition.generate(message,task_type or TaskType.GENERAL)
