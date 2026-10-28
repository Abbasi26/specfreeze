"""Data the stages pass to each other. JSON artifacts are plain dicts of these."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Followup:
    """A catalog follow-up. Triggers are AND-combined when both are set."""

    id: str
    category: str
    type: str
    ask: str
    options: dict[str, str] = field(default_factory=dict)
    levels: list[str] = field(default_factory=list)
    when_answer: list[str] = field(default_factory=list)
    when_confidence_below: float | None = None
    conservative: str | None = None
    contract_note: dict[str, str] = field(default_factory=dict)
    followups: list[Followup] = field(default_factory=list)


@dataclass
class QuestionTemplate:
    id: str
    category: str
    type: str
    ask: str
    scope: str = "function"
    applies_to: list[str] = field(default_factory=list)
    options: dict[str, str] = field(default_factory=dict)
    levels: list[str] = field(default_factory=list)
    conservative: str | None = None
    contract_note: dict[str, str] = field(default_factory=dict)
    followups: list[Followup] = field(default_factory=list)


@dataclass
class Catalog:
    name: str
    description: str
    questions: list[QuestionTemplate]
    version: int = 1
    max_per_function: int = 100


@dataclass
class Asked:
    """One concrete question after placeholders are filled in."""

    key: str
    id: str
    function: str | None
    category: str
    type: str
    ask: str
    scope: str
    options: dict[str, str] = field(default_factory=dict)
    levels: list[str] = field(default_factory=list)
    conservative: str | None = None
    contract_note: dict[str, str] = field(default_factory=dict)
    followups: list[Followup] = field(default_factory=list)
    round: int = 0
    parent: str | None = None


@dataclass
class Answer:
    key: str
    id: str
    function: str | None
    category: str
    type: str
    ask: str
    answer: str | None
    confidence: float
    rationale: str
    round: int
    effective: str | None
    effective_source: str
    p_yes: float | None = None
    options: dict[str, str] = field(default_factory=dict)
    levels: list[str] = field(default_factory=list)
    conservative: str | None = None
    contract_note: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "id": self.id,
            "function": self.function,
            "category": self.category,
            "type": self.type,
            "ask": self.ask,
            "answer": self.answer,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "round": self.round,
            "effective": self.effective,
            "effective_source": self.effective_source,
            "p_yes": self.p_yes,
            "options": self.options,
            "levels": self.levels,
            "conservative": self.conservative,
            "contract_note": self.contract_note,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Answer:
        return cls(
            key=data["key"],
            id=data["id"],
            function=data.get("function"),
            category=data.get("category", ""),
            type=data.get("type", "yes_no"),
            ask=data.get("ask", ""),
            answer=data.get("answer"),
            confidence=float(data.get("confidence") or 0),
            rationale=data.get("rationale") or "",
            round=int(data.get("round") or 0),
            effective=data.get("effective"),
            effective_source=data.get("effective_source") or "model",
            p_yes=data.get("p_yes"),
            options=dict(data.get("options") or {}),
            levels=list(data.get("levels") or []),
            conservative=data.get("conservative"),
            contract_note=dict(data.get("contract_note") or {}),
        )


@dataclass
class FunctionSpec:
    name: str
    signature: str
    purpose: str
    calls: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "signature": self.signature,
            "purpose": self.purpose,
            "calls": list(self.calls),
            "examples": list(self.examples),
        }

    @classmethod
    def from_dict(cls, data: dict) -> FunctionSpec:
        return cls(
            name=data["name"],
            signature=data.get("signature") or "",
            purpose=data.get("purpose") or "",
            calls=list(data.get("calls") or []),
            examples=list(data.get("examples") or []),
        )


@dataclass
class SpecDocument:
    title: str
    module: str
    summary: str
    constants: list[dict]
    imports: list[str]
    functions: list[FunctionSpec]
    raw: str

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "module": self.module,
            "summary": self.summary,
            "constants": self.constants,
            "imports": self.imports,
            "functions": [fn.to_dict() for fn in self.functions],
        }

    @classmethod
    def from_dict(cls, data: dict, raw: str = "") -> SpecDocument:
        return cls(
            title=data.get("title") or data.get("module") or "",
            module=data["module"],
            summary=data.get("summary") or "",
            constants=list(data.get("constants") or []),
            imports=list(data.get("imports") or []),
            functions=[FunctionSpec.from_dict(item) for item in data.get("functions") or []],
            raw=raw,
        )