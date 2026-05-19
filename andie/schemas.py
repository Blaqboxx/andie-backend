from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

class Signal(BaseModel):
    entity: str
    signal_type: str
    value: float
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainty: Optional[float] = None  # Optional, can be 1-confidence
    timestamp: datetime
    source: str
    metadata: Optional[Dict[str, Any]] = None

class Prediction(BaseModel):
    entity: str
    prediction_type: str
    value: float
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainty: Optional[float] = None
    timestamp: datetime
    model: str
    input_signals: List[Signal]
    metadata: Optional[Dict[str, Any]] = None

class Decision(BaseModel):
    entity: str
    decision_type: str
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainty: Optional[float] = None
    timestamp: datetime
    fused_from: List[Prediction]
    trace_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class Outcome(BaseModel):
    entity: str
    decision_type: str
    actual_value: Any
    correct: bool
    timestamp: datetime
    trace_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
