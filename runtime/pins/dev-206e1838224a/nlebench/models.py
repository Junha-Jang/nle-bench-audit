"""
NLEBench Core Models

Defines the core data structures for the NLEBench benchmark framework.
Based on Constraint Satisfaction approach inspired by WebArena.

v3: EditProject v4.3 schema
    - Unified Clip/Track (type/kind fields)
    - Rational time representation (lossless)
    - flat media/timelines + Bin reference structure
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from fractions import Fraction
from typing import Any, Literal, Optional, Union


# ============================================================
# Taxonomy Enums
# ============================================================

class Scale(str, Enum):
    """Task scale (complexity by number of operations)."""
    L1 = "L1"  # Single: one tool call
    L2 = "L2"  # Compound: 2-5 tool calls, sequential
    L3 = "L3"  # Batch: 6+ tool calls, cross-track or batch


class CogType(str, Enum):
    """Cognitive type required to solve the task."""
    B = "B"  # Basic: direct parameter mapping
    A = "A"  # Adapt: anaphoric reference, context carry-over
    R = "R"  # Reason: state reasoning, temporal logic
    E = "E"  # Execute: multi-step execution planning
    P = "P"  # Plan: dependency-aware planning
    I = "I"  # Integrate: iterative refinement, multi-turn


class Feasibility(str, Enum):
    """Whether the task is feasible for the agent."""
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    AMBIGUOUS = "ambiguous"


class BenchmarkTrack(str, Enum):
    """Benchmark evaluation track.

    - CANONICAL: Agent uses 25 canonical tools, we execute tool calls
    - OPEN: Agent directly produces final EditProject state
    - CANONICAL_MULTITURN: closed-loop variant of CANONICAL — tool results are
      fed back to the model between turns. Its numbers are NOT comparable with
      CANONICAL and must never be pooled with them (see ExecutionResult.track).
    - CANONICAL_MULTITURN_NOTOOL: the V0 control arm of the V×U intervention
      experiment (stage-8 design packet). Same closed-loop episode shape,
      budgets and stop rules as CANONICAL_MULTITURN, but the agent receives no
      tool schema and nothing it emits is ever executed; the feedback slot
      carries a deterministic project-state summary instead of tool results.
      Comparable ONLY with CANONICAL_MULTITURN inside the V×U design — never
      pooled with any published track.
    """
    CANONICAL = "canonical"
    OPEN = "open"
    CANONICAL_MULTITURN = "canonical_multiturn"
    CANONICAL_MULTITURN_NOTOOL = "canonical_multiturn_notool"


#: Tracks produced by the closed-loop harness. Only rows from these tracks may
#: carry a ``multiturn_trace``.
CLOSED_LOOP_TRACK_VALUES = frozenset({
    BenchmarkTrack.CANONICAL_MULTITURN.value,
    BenchmarkTrack.CANONICAL_MULTITURN_NOTOOL.value,
})


@dataclass
class Taxonomy:
    """Dual taxonomy: v2 (Scale × CogType) + v3 (Information × Action) × Feasibility."""
    # v2 axes
    scale: Scale
    cognitive_type: Optional[CogType] = None  # null for infeasible/ambiguous
    cognitive_type_secondary: Optional[CogType] = None
    feasibility: Feasibility = Feasibility.FEASIBLE
    # v3 axes
    information: Optional[str] = None  # "explicit" | "state" | "context" | "diagnosis"
    action: Optional[str] = None  # "atomic" | "compound" | "dependent" | "cumulative"

    def to_dict(self) -> dict:
        d = {
            "scale": self.scale.value,
            "cognitive_type": self.cognitive_type.value if self.cognitive_type else None,
            "cognitive_type_secondary": (
                self.cognitive_type_secondary.value if self.cognitive_type_secondary else None
            ),
            "feasibility": self.feasibility.value,
        }
        if self.information:
            d["information"] = self.information
        if self.action:
            d["action"] = self.action
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Taxonomy":
        return cls(
            scale=Scale(data["scale"]),
            cognitive_type=CogType(data["cognitive_type"]) if data.get("cognitive_type") else None,
            cognitive_type_secondary=(
                CogType(data["cognitive_type_secondary"])
                if data.get("cognitive_type_secondary")
                else None
            ),
            feasibility=Feasibility(data.get("feasibility", "feasible")),
            information=data.get("information"),
            action=data.get("action"),
        )


# ============================================================
# Legacy Taxonomy (backwards compat with existing 600 scenarios)
# ============================================================

class Level(str, Enum):
    """Task complexity levels (legacy — use Scale + CogType instead)."""
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L4a = "L4a"
    L4b = "L4b"

    @property
    def base_level(self) -> str:
        """Return base level (L4a/L4b -> L4)."""
        if self.value.startswith("L4"):
            return "L4"
        return self.value

    def to_scale(self) -> Scale:
        """Convert legacy Level to Scale (L4* maps to L2 as multi-turn)."""
        mapping = {"L1": Scale.L1, "L2": Scale.L2, "L3": Scale.L3}
        return mapping.get(self.value, Scale.L2)


# ============================================================
# EditProject v4.3 Primitives
# ============================================================

@dataclass
class Rational:
    """무손실 유리수 시간 표현. {"n": 30000, "d": 1001}"""
    n: int  # numerator
    d: int  # denominator (>0)

    def to_float(self) -> float:
        return self.n / self.d

    def to_dict(self) -> dict:
        return {"n": self.n, "d": self.d}

    @classmethod
    def from_float(cls, value: float, precision: int = 1000000) -> "Rational":
        """float를 Rational로 변환 (정밀도 제한)."""
        frac = Fraction(value).limit_denominator(precision)
        return cls(n=frac.numerator, d=frac.denominator)

    @classmethod
    def from_dict(cls, data: dict) -> "Rational":
        return cls(n=data["n"], d=data["d"])


@dataclass
class NativeBlock:
    """NLE-specific 데이터 보존."""
    source: str  # "premiere" | "fcp" | "davinci"
    type: str    # "graphic-style-preset" | "lumetri-color"
    encoding: Optional[str] = None  # "json" | "base64" | None
    data: Any = None

    def to_dict(self) -> dict:
        return {"source": self.source, "type": self.type, "encoding": self.encoding, "data": self.data}

    @classmethod
    def from_dict(cls, data: dict) -> "NativeBlock":
        return cls(source=data["source"], type=data["type"], encoding=data.get("encoding"), data=data.get("data"))


# ============================================================
# Media
# ============================================================

@dataclass
class VideoProperties:
    """미디어의 영상 속성."""
    width: int
    height: int
    fps: Rational
    codec: Optional[str] = None

    def to_dict(self) -> dict:
        d = {"width": self.width, "height": self.height, "fps": self.fps.to_dict()}
        if self.codec:
            d["codec"] = self.codec
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "VideoProperties":
        return cls(
            width=data["width"], height=data["height"],
            fps=Rational.from_dict(data["fps"]) if isinstance(data["fps"], dict) else Rational.from_float(data["fps"]),
            codec=data.get("codec"),
        )


@dataclass
class AudioProperties:
    """미디어의 오디오 속성."""
    sample_rate: int = 48000
    bit_depth: int = 24
    channels: int = 2

    def to_dict(self) -> dict:
        return {"sample_rate": self.sample_rate, "bit_depth": self.bit_depth, "channels": self.channels}

    @classmethod
    def from_dict(cls, data: dict) -> "AudioProperties":
        return cls(
            sample_rate=data.get("sample_rate", 48000),
            bit_depth=data.get("bit_depth", 24),
            channels=data.get("channels", 2),
        )


@dataclass
class Media:
    """미디어 소스 (파일 또는 generator)."""
    id: str
    name: str
    type: Literal["video", "audio", "image", "generator"]
    path: Optional[str] = None
    duration: Optional[Rational] = None
    video: Optional[VideoProperties] = None
    audio: Optional[AudioProperties] = None
    generator_type: Optional[str] = None  # when type="generator"
    native: list["NativeBlock"] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {"id": self.id, "name": self.name, "type": self.type}
        if self.path:
            d["path"] = self.path
        if self.duration:
            d["duration"] = self.duration.to_dict()
        if self.video:
            d["video"] = self.video.to_dict()
        if self.audio:
            d["audio"] = self.audio.to_dict()
        if self.generator_type:
            d["generator_type"] = self.generator_type
        if self.native:
            d["native"] = [n.to_dict() for n in self.native]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Media":
        duration = None
        if data.get("duration"):
            dur = data["duration"]
            duration = Rational.from_dict(dur) if isinstance(dur, dict) else Rational.from_float(dur)
        return cls(
            id=data["id"], name=data["name"], type=data["type"],
            path=data.get("path"),
            duration=duration,
            video=VideoProperties.from_dict(data["video"]) if data.get("video") else None,
            audio=AudioProperties.from_dict(data["audio"]) if data.get("audio") else None,
            generator_type=data.get("generator_type"),
            native=[NativeBlock.from_dict(n) for n in data.get("native", [])],
        )


# ============================================================
# Bin
# ============================================================

@dataclass
class Bin:
    """미디어/타임라인 폴더 (재귀 구조)."""
    id: str
    name: str
    media_ids: list[str] = field(default_factory=list)
    timeline_ids: list[str] = field(default_factory=list)
    bins: list["Bin"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name,
            "media_ids": self.media_ids,
            "timeline_ids": self.timeline_ids,
            "bins": [b.to_dict() for b in self.bins],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Bin":
        return cls(
            id=data["id"], name=data["name"],
            media_ids=data.get("media_ids", []),
            timeline_ids=data.get("timeline_ids", []),
            bins=[Bin.from_dict(b) for b in data.get("bins", [])],
        )


# ============================================================
# Text / Caption / Title
# ============================================================

@dataclass
class TextStyle:
    """텍스트 스타일."""
    font_family: Optional[str] = None
    font_size: Optional[float] = None
    font_color: str = "#FFFFFF"
    bold: bool = False
    italic: bool = False
    align: str = "center"  # "left" | "center" | "right"
    vertical_align: Optional[str] = None  # "top" | "middle" | "bottom"

    def to_dict(self) -> dict:
        d = {"font_color": self.font_color, "bold": self.bold, "italic": self.italic, "align": self.align}
        if self.font_family:
            d["font_family"] = self.font_family
        if self.font_size:
            d["font_size"] = self.font_size
        if self.vertical_align:
            d["vertical_align"] = self.vertical_align
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "TextStyle":
        return cls(
            font_family=data.get("font_family"),
            font_size=data.get("font_size"),
            font_color=data.get("font_color", "#FFFFFF"),
            bold=data.get("bold", False),
            italic=data.get("italic", False),
            align=data.get("align", "center"),
            vertical_align=data.get("vertical_align"),
        )


@dataclass
class Title:
    """그래픽 타이틀 콘텐츠."""
    text: str
    style: Optional[TextStyle] = None

    def to_dict(self) -> dict:
        d = {"text": self.text}
        if self.style:
            d["style"] = self.style.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Title":
        return cls(
            text=data["text"],
            style=TextStyle.from_dict(data["style"]) if data.get("style") else None,
        )


@dataclass
class Caption:
    """자막 콘텐츠."""
    text: str
    style: Optional[TextStyle] = None

    def to_dict(self) -> dict:
        d = {"text": self.text}
        if self.style:
            d["style"] = self.style.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Caption":
        return cls(
            text=data["text"],
            style=TextStyle.from_dict(data["style"]) if data.get("style") else None,
        )


# ============================================================
# Speed (Core: speed_map, Extended: frame_interpolation)
# ============================================================

@dataclass
class SpeedPoint:
    """속도 제어점. rate=1.0이 정상 속도, 음수는 역재생."""
    time: Rational  # time within the clip
    rate: float = 1.0
    easing: str = "linear"  # "linear" | "ease-in" | "ease-out" | "ease-in-out"

    def to_dict(self) -> dict:
        return {"time": self.time.to_dict(), "rate": self.rate, "easing": self.easing}

    @classmethod
    def from_dict(cls, data: dict) -> "SpeedPoint":
        t = data["time"]
        return cls(
            time=Rational.from_dict(t) if isinstance(t, dict) else Rational.from_float(t),
            rate=data.get("rate", 1.0),
            easing=data.get("easing", "linear"),
        )


@dataclass
class Speed:
    """속도/리타임 설정. speed_map=[]이면 정상 속도(1x)."""
    speed_map: list[SpeedPoint] = field(default_factory=list)
    frame_interpolation: Optional[str] = None  # Extended: "nearest"|"blend"|"optical_flow"

    def to_dict(self) -> dict:
        d: dict = {"speed_map": [sp.to_dict() for sp in self.speed_map]}
        if self.frame_interpolation:
            d["frame_interpolation"] = self.frame_interpolation
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Speed":
        return cls(
            speed_map=[SpeedPoint.from_dict(sp) for sp in data.get("speed_map", [])],
            frame_interpolation=data.get("frame_interpolation"),
        )

    @property
    def effective_rate(self) -> float:
        """단일 속도값 반환. speed_map이 비거나 1개면 해당 rate, 여러 개면 첫 번째."""
        if not self.speed_map:
            return 1.0
        return self.speed_map[0].rate


# ============================================================
# ClipAudio (Core: volume, pan, muted; Extended: normalize)
# ============================================================

@dataclass
class ClipAudio:
    """클립 레벨 오디오 속성."""
    volume: float = 0.0  # dB, 0.0 = unity gain
    pan: float = 0.0  # -1.0 (L) to +1.0 (R)
    muted: bool = False
    channels: Optional[list[int]] = None  # 0-based source channel indices
    # Extended
    normalize_mode: Optional[str] = None  # "peak" | "loudness"
    normalize_target: Optional[float] = None  # dBFS or LUFS

    def to_dict(self) -> dict:
        d: dict = {"volume": self.volume, "pan": self.pan, "muted": self.muted}
        if self.channels is not None:
            d["channels"] = self.channels
        if self.normalize_mode:
            d["normalize_mode"] = self.normalize_mode
        if self.normalize_target is not None:
            d["normalize_target"] = self.normalize_target
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "ClipAudio":
        return cls(
            volume=data.get("volume", 0.0),
            pan=data.get("pan", 0.0),
            muted=data.get("muted", False),
            channels=data.get("channels"),
            normalize_mode=data.get("normalize_mode"),
            normalize_target=data.get("normalize_target"),
        )


# ============================================================
# Transform (Core)
# ============================================================

@dataclass
class Transform:
    """공간 변환. Core: position, scale, rotation, opacity, anchor, skew, crop."""
    position_x: float = 0.5  # normalized coordinates (0.0-1.0)
    position_y: float = 0.5
    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation: float = 0.0  # degrees
    opacity: float = 1.0   # 0.0~1.0
    anchor_x: float = 0.5  # anchor point for rotation/scale
    anchor_y: float = 0.5
    skew: float = 0.0  # degrees
    crop_top: float = 0.0  # ratio 0.0-1.0
    crop_bottom: float = 0.0
    crop_left: float = 0.0
    crop_right: float = 0.0

    def to_dict(self) -> dict:
        d = {
            "position_x": self.position_x, "position_y": self.position_y,
            "scale_x": self.scale_x, "scale_y": self.scale_y,
            "rotation": self.rotation, "opacity": self.opacity,
        }
        # serialize only non-default fields (backward compat)
        if self.anchor_x != 0.5 or self.anchor_y != 0.5:
            d["anchor_x"] = self.anchor_x
            d["anchor_y"] = self.anchor_y
        if self.skew != 0.0:
            d["skew"] = self.skew
        if any(v != 0.0 for v in [self.crop_top, self.crop_bottom, self.crop_left, self.crop_right]):
            d["crop_top"] = self.crop_top
            d["crop_bottom"] = self.crop_bottom
            d["crop_left"] = self.crop_left
            d["crop_right"] = self.crop_right
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Transform":
        return cls(
            position_x=data.get("position_x", 0.5), position_y=data.get("position_y", 0.5),
            scale_x=data.get("scale_x", 1.0), scale_y=data.get("scale_y", 1.0),
            rotation=data.get("rotation", 0.0), opacity=data.get("opacity", 1.0),
            anchor_x=data.get("anchor_x", 0.5), anchor_y=data.get("anchor_y", 0.5),
            skew=data.get("skew", 0.0),
            crop_top=data.get("crop_top", 0.0), crop_bottom=data.get("crop_bottom", 0.0),
            crop_left=data.get("crop_left", 0.0), crop_right=data.get("crop_right", 0.0),
        )


# ============================================================
# Clip
# ============================================================

@dataclass
class Clip:
    """통합 클립 (video, audio, title, caption, gap 등).

    CENS Tier 배정:
    - Core (항상 present): id, type, name, timeline_start, duration, enabled, link_group, native
    - Core (type 의존): ref_id, ref_type, source_in, source_out, transform, audio
    - Extended: title, caption, speed, blend_mode, label
    """
    id: str
    type: Literal["video", "audio", "title", "caption", "timeline", "adjustment", "gap"]
    name: str
    timeline_start: Rational
    duration: Rational
    enabled: bool = True
    link_group: Optional[str] = None

    # Source reference (Core, type-dependent)
    ref_id: Optional[str] = None      # Media.id or Timeline.id
    ref_type: Optional[str] = None    # "media" | "timeline"

    # Trim points (Core, type-dependent)
    source_in: Optional[Rational] = None
    source_out: Optional[Rational] = None

    # Spatial (Core, type-dependent: video/title/caption)
    transform: Optional[Transform] = None

    # Audio (Core, type-dependent: video/audio clips)
    audio: Optional[ClipAudio] = None

    # Legacy flat audio fields (backward compat, read-only; the audio sub-object takes precedence)
    volume: float = 0.0  # dB — deprecated, use audio.volume
    muted: bool = False   # deprecated, use audio.muted

    # Speed (Core: speed_map, Extended: frame_interpolation)
    speed: Optional[Speed] = None

    # Content (Extended, type-dependent)
    title: Optional[Title] = None
    caption: Optional[Caption] = None

    # Metadata (Extended)
    label: Optional[str] = None
    native: list[NativeBlock] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {
            "id": self.id, "type": self.type, "name": self.name,
            "timeline_start": self.timeline_start.to_dict(),
            "duration": self.duration.to_dict(),
            "enabled": self.enabled,
        }
        if self.link_group:
            d["link_group"] = self.link_group
        if self.ref_id:
            d["ref_id"] = self.ref_id
            d["ref_type"] = self.ref_type
        if self.source_in:
            d["source_in"] = self.source_in.to_dict()
        if self.source_out:
            d["source_out"] = self.source_out.to_dict()
        if self.transform:
            d["transform"] = self.transform.to_dict()
        # Audio: prefer sub-object, fallback to legacy flat fields
        if self.audio:
            d["audio"] = self.audio.to_dict()
        elif self.volume != 0.0 or self.muted:
            d["volume"] = self.volume
            d["muted"] = self.muted
        if self.speed:
            d["speed"] = self.speed.to_dict()
        if self.title:
            d["title"] = self.title.to_dict()
        if self.caption:
            d["caption"] = self.caption.to_dict()
        if self.label:
            d["label"] = self.label
        if self.native:
            d["native"] = [n.to_dict() for n in self.native]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Clip":
        def _parse_rational(val):
            if val is None:
                return None
            return Rational.from_dict(val) if isinstance(val, dict) else Rational.from_float(val)

        # Parse audio: prefer sub-object, fallback to legacy flat fields
        audio = None
        if data.get("audio") and isinstance(data["audio"], dict):
            audio = ClipAudio.from_dict(data["audio"])
        elif data.get("volume", 0.0) != 0.0 or data.get("muted", False):
            audio = ClipAudio(volume=data.get("volume", 0.0), muted=data.get("muted", False))

        return cls(
            id=data["id"], type=data["type"], name=data["name"],
            timeline_start=_parse_rational(data["timeline_start"]),
            duration=_parse_rational(data["duration"]),
            enabled=data.get("enabled", True),
            link_group=data.get("link_group"),
            ref_id=data.get("ref_id"),
            ref_type=data.get("ref_type"),
            source_in=_parse_rational(data.get("source_in")),
            source_out=_parse_rational(data.get("source_out")),
            transform=Transform.from_dict(data["transform"]) if data.get("transform") else None,
            audio=audio,
            volume=data.get("volume", 0.0),
            muted=data.get("muted", False),
            speed=Speed.from_dict(data["speed"]) if data.get("speed") else None,
            title=Title.from_dict(data["title"]) if data.get("title") else None,
            caption=Caption.from_dict(data["caption"]) if data.get("caption") else None,
            label=data.get("label"),
            native=[NativeBlock.from_dict(n) for n in data.get("native", [])],
        )

    # Convenience properties for constraint evaluation
    @property
    def start(self) -> float:
        """Legacy alias: timeline_start as float seconds."""
        return self.timeline_start.to_float()

    @property
    def end(self) -> float:
        """Legacy alias: timeline_start + duration as float seconds."""
        return self.timeline_start.to_float() + self.duration.to_float()

    @property
    def text(self) -> Optional[str]:
        """Get text content from caption or title."""
        if self.caption:
            return self.caption.text
        if self.title:
            return self.title.text
        return None

    @property
    def effective_volume(self) -> float:
        """Get volume from audio sub-object or legacy flat field."""
        if self.audio:
            return self.audio.volume
        return self.volume

    @property
    def effective_muted(self) -> bool:
        """Get muted from audio sub-object or legacy flat field."""
        if self.audio:
            return self.audio.muted
        return self.muted

    @property
    def effective_speed(self) -> float:
        """Get effective speed rate (1.0 = normal)."""
        if self.speed:
            return self.speed.effective_rate
        return 1.0


# ============================================================
# Transition
# ============================================================

@dataclass
class Transition:
    """전환 효과."""
    id: str
    type: str  # "cross_dissolve" | "constant_power" | etc.
    duration: Rational
    clip_before_id: Optional[str] = None
    clip_after_id: Optional[str] = None

    def to_dict(self) -> dict:
        d = {"id": self.id, "type": self.type, "duration": self.duration.to_dict()}
        if self.clip_before_id:
            d["clip_before_id"] = self.clip_before_id
        if self.clip_after_id:
            d["clip_after_id"] = self.clip_after_id
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Transition":
        dur = data["duration"]
        return cls(
            id=data["id"], type=data["type"],
            duration=Rational.from_dict(dur) if isinstance(dur, dict) else Rational.from_float(dur),
            clip_before_id=data.get("clip_before_id"),
            clip_after_id=data.get("clip_after_id"),
        )


# ============================================================
# Track
# ============================================================

@dataclass
class Track:
    """통합 트랙 (video, audio, caption)."""
    id: str
    kind: Literal["video", "audio", "caption"]
    name: str
    locked: bool = False
    visible: bool = True
    muted: bool = False  # audio track only
    solo: bool = False   # audio track only
    clips: list[Clip] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    native: list[NativeBlock] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "id": self.id, "kind": self.kind, "name": self.name,
            "locked": self.locked, "visible": self.visible,
            "clips": [c.to_dict() for c in self.clips],
            "transitions": [t.to_dict() for t in self.transitions],
        }
        if self.kind == "audio":
            d["muted"] = self.muted
            d["solo"] = self.solo
        if self.native:
            d["native"] = [n.to_dict() for n in self.native]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Track":
        return cls(
            id=data["id"], kind=data["kind"], name=data["name"],
            locked=data.get("locked", False), visible=data.get("visible", True),
            muted=data.get("muted", False), solo=data.get("solo", False),
            clips=[Clip.from_dict(c) for c in data.get("clips", [])],
            transitions=[Transition.from_dict(t) for t in data.get("transitions", [])],
            native=[NativeBlock.from_dict(n) for n in data.get("native", [])],
        )


# ============================================================
# Timeline
# ============================================================

@dataclass
class Timeline:
    """타임라인 (시퀀스)."""
    id: str
    name: str
    width: int = 1920
    height: int = 1080
    fps: Rational = field(default_factory=lambda: Rational(n=30000, d=1001))
    sample_rate: int = 48000
    channel_layout: str = "stereo"  # Core: open str
    color_space: str = "Rec.709"    # Core: open str
    tracks: list[Track] = field(default_factory=list)
    native: list[NativeBlock] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "id": self.id, "name": self.name,
            "width": self.width, "height": self.height,
            "fps": self.fps.to_dict(), "sample_rate": self.sample_rate,
            "channel_layout": self.channel_layout,
            "color_space": self.color_space,
            "tracks": [t.to_dict() for t in self.tracks],
            "native": [n.to_dict() for n in self.native] if self.native else [],
        }
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Timeline":
        fps = data.get("fps", {"n": 30000, "d": 1001})
        return cls(
            id=data["id"], name=data["name"],
            width=data.get("width", 1920), height=data.get("height", 1080),
            fps=Rational.from_dict(fps) if isinstance(fps, dict) else Rational.from_float(fps),
            sample_rate=data.get("sample_rate", 48000),
            channel_layout=data.get("channel_layout", "stereo"),
            color_space=data.get("color_space", "Rec.709"),
            tracks=[Track.from_dict(t) for t in data.get("tracks", [])],
            native=[NativeBlock.from_dict(n) for n in data.get("native", [])],
        )

    # Convenience methods for constraint evaluation
    def get_clips_by_type(self, clip_type: str) -> list[Clip]:
        """Get all clips of a specific type across all tracks."""
        result = []
        for track in self.tracks:
            for clip in track.clips:
                if clip.type == clip_type:
                    result.append(clip)
        return result

    def get_tracks_by_kind(self, kind: str) -> list[Track]:
        """Get all tracks of a specific kind."""
        return [t for t in self.tracks if t.kind == kind]

    @property
    def video_tracks(self) -> list[Track]:
        return self.get_tracks_by_kind("video")

    @property
    def audio_tracks(self) -> list[Track]:
        return self.get_tracks_by_kind("audio")

    @property
    def caption_tracks(self) -> list[Track]:
        return self.get_tracks_by_kind("caption")

    @property
    def video_clips(self) -> list[Clip]:
        return self.get_clips_by_type("video")

    @property
    def audio_clips(self) -> list[Clip]:
        return self.get_clips_by_type("audio")

    @property
    def captions(self) -> list[Clip]:
        return self.get_clips_by_type("caption")


# ============================================================
# EditProject (root state container) - v4.3
# ============================================================

@dataclass
class EditProject:
    """
    Universal 편집 프로젝트 (v4.3).

    구조:
    - bin: 조직화 구조 (ID 참조만)
    - media: flat list (실제 미디어 데이터)
    - timelines: flat list (실제 타임라인 데이터)
    """
    schema_version: str = "4.3"
    title: str = ""
    bin: Bin = field(default_factory=lambda: Bin(id="root", name="Root"))
    media: list[Media] = field(default_factory=list)
    timelines: list[Timeline] = field(default_factory=list)
    native: list[NativeBlock] = field(default_factory=list)

    # Snapshot of initial state (set by harness before agent execution)
    _initial: Optional["EditProject"] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            "schema_version": self.schema_version,
            "title": self.title,
            "bin": self.bin.to_dict(),
            "media": [m.to_dict() for m in self.media],
            "timelines": [t.to_dict() for t in self.timelines],
            "native": [n.to_dict() for n in self.native] if self.native else [],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EditProject":
        """Load from JSON-compatible dict."""
        return cls(
            schema_version=data.get("schema_version", "4.3"),
            title=data.get("title", ""),
            bin=Bin.from_dict(data["bin"]) if data.get("bin") else Bin(id="root", name="Root"),
            media=[Media.from_dict(m) for m in data.get("media", [])],
            timelines=[Timeline.from_dict(t) for t in data.get("timelines", [])],
            native=[NativeBlock.from_dict(n) for n in data.get("native", [])],
        )

    @classmethod
    def from_json(cls, json_str: str) -> "EditProject":
        """Load from JSON string."""
        import json
        return cls.from_dict(json.loads(json_str))

    def get_media_by_id(self, media_id: str) -> Optional[Media]:
        """Look up media by ID."""
        for m in self.media:
            if m.id == media_id:
                return m
        return None

    def get_timeline_by_id(self, timeline_id: str) -> Optional[Timeline]:
        """Look up timeline by ID."""
        for t in self.timelines:
            if t.id == timeline_id:
                return t
        return None

    def get_clip_by_id(self, clip_id: str) -> Optional[Clip]:
        """Look up clip by ID across all timelines."""
        for timeline in self.timelines:
            for track in timeline.tracks:
                for clip in track.clips:
                    if clip.id == clip_id:
                        return clip
        return None

    def collect_all_ids(self) -> list[str]:
        """Collect all entity IDs in the project."""
        ids: list[str] = [self.bin.id]
        ids.extend(m.id for m in self.media)
        for timeline in self.timelines:
            ids.append(timeline.id)
            for track in timeline.tracks:
                ids.append(track.id)
                for clip in track.clips:
                    ids.append(clip.id)
                    # Effect IDs live in the NativeBlock payload. Leaving them
                    # out made `_next_id(state, "effect")` hand out `effect_1`
                    # every time, so a project with two effects had two entities
                    # sharing one ID and `update_effect`/`remove_effect` could
                    # only ever reach the first.
                    for nb in clip.native:
                        data = getattr(nb, "data", None)
                        if isinstance(data, dict) and isinstance(data.get("id"), str):
                            ids.append(data["id"])
                ids.extend(t.id for t in track.transitions)
        return ids

    # ============================================================
    # Convenience properties for constraint evaluation (legacy compat)
    # ============================================================

    @property
    def all_clips(self) -> list[Clip]:
        """All clips across all timelines (unified collection)."""
        result = []
        for t in self.timelines:
            for track in t.tracks:
                result.extend(track.clips)
        return result

    @property
    def video_clips(self) -> list[Clip]:
        """All video clips across all timelines."""
        result = []
        for t in self.timelines:
            result.extend(t.video_clips)
        return result

    @property
    def audio_clips(self) -> list[Clip]:
        """All audio clips across all timelines."""
        result = []
        for t in self.timelines:
            result.extend(t.audio_clips)
        return result

    @property
    def captions(self) -> list[Clip]:
        """All caption clips across all timelines."""
        result = []
        for t in self.timelines:
            result.extend(t.captions)
        return result

    @property
    def video_tracks(self) -> list[Track]:
        """All video tracks across all timelines."""
        result = []
        for t in self.timelines:
            result.extend(t.video_tracks)
        return result

    @property
    def audio_tracks(self) -> list[Track]:
        """All audio tracks across all timelines."""
        result = []
        for t in self.timelines:
            result.extend(t.audio_tracks)
        return result

    @property
    def sequences(self) -> list[Timeline]:
        """Alias for timelines (legacy compat)."""
        return self.timelines

    @property
    def bins(self) -> list[Bin]:
        """Flatten nested bins for legacy compat."""
        result = []
        def _collect(b: Bin):
            result.append(b)
            for child in b.bins:
                _collect(child)
        _collect(self.bin)
        return result

    @property
    def av_medias(self) -> list[Media]:
        """Media with type='video' (legacy compat)."""
        return [m for m in self.media if m.type == "video"]

    @property
    def video_medias(self) -> list[Media]:
        """Media with type='video' (legacy compat, same as av_medias)."""
        return [m for m in self.media if m.type == "video"]

    @property
    def audio_medias(self) -> list[Media]:
        """Media with type='audio' (legacy compat)."""
        return [m for m in self.media if m.type == "audio"]

    @property
    def effects(self) -> list[NativeBlock]:
        """Effects stored as NativeBlocks on clips (legacy compat)."""
        result = []
        for t in self.timelines:
            for track in t.tracks:
                for clip in track.clips:
                    for nb in clip.native:
                        if nb.type not in ("source", "metadata"):
                            result.append(nb)
        return result

    @property
    def transitions(self) -> list[Transition]:
        """All transitions across all tracks (legacy compat)."""
        result = []
        for t in self.timelines:
            for track in t.tracks:
                result.extend(track.transitions)
        return result

    @property
    def links(self) -> list[dict]:
        """Link groups as legacy-style dicts (legacy compat)."""
        # In v4.3, links are via clip.link_group field
        # Group clips by link_group and return as list of dicts
        link_map: dict[str, list[str]] = {}
        for t in self.timelines:
            for track in t.tracks:
                for clip in track.clips:
                    if clip.link_group:
                        if clip.link_group not in link_map:
                            link_map[clip.link_group] = []
                        link_map[clip.link_group].append(clip.id)
        return [
            {"id": lg, "clip_ids": ids}
            for lg, ids in link_map.items()
        ]

    def get_entity_by_id(self, entity_id: str) -> Any:
        """Look up any entity by ID (legacy compat)."""
        # Check clips
        clip = self.get_clip_by_id(entity_id)
        if clip:
            return clip
        # Check media
        media = self.get_media_by_id(entity_id)
        if media:
            return media
        # Check timelines
        timeline = self.get_timeline_by_id(entity_id)
        if timeline:
            return timeline
        # Check tracks
        for tl in self.timelines:
            for track in tl.tracks:
                if track.id == entity_id:
                    return track
        # Check bins
        for b in self.bins:
            if b.id == entity_id:
                return b
        # Check transitions
        for tr in self.transitions:
            if tr.id == entity_id:
                return tr
        # Check effects. These are NativeBlocks whose identity lives in
        # `data["id"]`, so every lookup above misses them: a constraint naming an
        # effect could never resolve, and 23 scenarios were unpassable for that
        # reason alone.
        for nb in self.effects:
            data = getattr(nb, "data", None)
            if isinstance(data, dict) and data.get("id") == entity_id:
                return nb
        return None


# ============================================================
# Constraint System (legacy — Phase 4 will add named constraints)
# ============================================================

class ConstraintType(str, Enum):
    """Types of constraints"""
    REQUIRED = "required"
    SPECIFIED = "specified"
    VALIDITY = "validity"


class Operator(str, Enum):
    """Constraint operators (legacy JSONPath-like)"""
    EQUALS = "equals"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"
    GTE = "gte"
    LTE = "lte"
    GT = "gt"
    LT = "lt"
    PATTERN = "pattern"
    COUNT = "count"
    COUNT_CHANGED = "count_changed"
    NO_OVERLAP = "no_overlap"
    COMPILE_SUCCESS = "compile_success"
    ALIGNED_TO_CLIPS = "aligned_to_clips"
    CONTAINS = "contains"

    # Per-turn comparative operators
    UNCHANGED_FROM_PREVIOUS = "unchanged_from_previous"
    COUNT_INCREASED = "count_increased"
    GREATER_THAN_PREVIOUS = "greater_than_previous"
    LESS_THAN_PREVIOUS = "less_than_previous"


@dataclass
class Constraint:
    """
    A single constraint to validate against the final EditState.
    (Legacy format — new scenarios use named constraint functions.)
    """
    type: ConstraintType
    field: str
    operator: Operator
    value: Any
    tolerance: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "field": self.field,
            "operator": self.operator.value,
            "value": self.value,
            "tolerance": self.tolerance,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Constraint":
        return cls(
            type=ConstraintType(data["type"]),
            field=data["field"],
            operator=Operator(data["operator"]),
            value=data["value"],
            tolerance=data.get("tolerance"),
        )


# ============================================================
# Scenario Models
# ============================================================

@dataclass
class Turn:
    """A single turn in a multi-turn scenario."""
    user: str
    fallback: Optional[str] = None
    extract: dict[str, str] = field(default_factory=dict)
    constraints_after_turn: list[Constraint] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {"user": self.user}
        if self.fallback:
            d["fallback"] = self.fallback
        if self.extract:
            d["extract"] = self.extract
        if self.constraints_after_turn:
            d["constraints_after_turn"] = [c.to_dict() for c in self.constraints_after_turn]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Turn":
        constraints = [
            Constraint.from_dict(c) for c in data.get("constraints_after_turn", [])
        ]
        return cls(
            user=data["user"],
            fallback=data.get("fallback"),
            extract=data.get("extract", {}),
            constraints_after_turn=constraints,
        )


@dataclass
class Scenario:
    """
    A benchmark scenario with user message and constraints.
    Supports both legacy (Level-based) and new (3D Taxonomy) formats.
    """
    id: str
    name: str
    level: Level
    category: str
    description: str
    fixture: str  # Name of the fixture (or dict with base+patch)

    user_messages: list[str]

    # Legacy constraints (Phase 4 will add named constraint support)
    required_constraints: list[Constraint] = field(default_factory=list)
    specified_constraints: list[Constraint] = field(default_factory=list)
    validity_constraints: list[Constraint] = field(default_factory=list)

    # OVR: entities expected to be changed
    expected_changed_entities: list[str] = field(default_factory=list)

    # --- 3D Taxonomy (new) ---
    taxonomy: Optional[Taxonomy] = None

    # --- Legacy taxonomy fields (backwards compat) ---
    scope: Literal["sequence", "project"] = "sequence"
    feasibility: Literal["feasible", "infeasible", "ambiguous"] = "feasible"

    # Infeasible scenarios
    required_capability: Optional[str] = None
    expected_behavior: Optional[str] = None  # "refuse" | "clarify"

    # Ambiguous scenarios
    ambiguity_type: Optional[str] = None
    required_clarifications: list[str] = field(default_factory=list)
    optional_clarifications: list[str] = field(default_factory=list)
    missing_parameters: Optional[list[str]] = None  # for CQS F1 calculation

    # Multi-turn structure
    turns: Optional[list[Turn]] = None

    # Named constraints (new format — list of dicts like [{func_name: {params}}])
    named_constraints_required: list[dict] = field(default_factory=list)
    named_constraints_specified: list[dict] = field(default_factory=list)
    tolerance_override: Optional[dict] = None

    # Gold intent & reference solution (new format)
    gold_intent: Optional[dict] = None
    reference_solution: Optional[list[dict]] = None

    # Contamination defense
    canary_string: Optional[str] = None
    perturb_fixture: bool = False

    # Metadata
    version: int = 1
    split: Optional[str] = None  # "dev" | "test"
    max_turns: int = 10
    timeout_seconds: float = 60.0
    requires_ab_test: bool = False
    tags: list[str] = field(default_factory=list)
    metadata: Optional[dict] = None

    @property
    def all_constraints(self) -> list[Constraint]:
        """Get all legacy constraints."""
        return (
            self.required_constraints
            + self.specified_constraints
            + self.validity_constraints
        )

    @property
    def effective_taxonomy(self) -> Taxonomy:
        """Get taxonomy (from explicit taxonomy or inferred from legacy level)."""
        if self.taxonomy:
            return self.taxonomy
        return Taxonomy(
            scale=self.level.to_scale(),
            feasibility=Feasibility(self.feasibility),
        )

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "level": self.level.value,
            "category": self.category,
            "description": self.description,
            "fixture": self.fixture,
            "user_messages": self.user_messages,
            "constraints": {
                "required": [c.to_dict() for c in self.required_constraints],
                "specified": [c.to_dict() for c in self.specified_constraints],
                "validity": [c.to_dict() for c in self.validity_constraints],
            },
            "expected_changed_entities": self.expected_changed_entities,
            "scope": self.scope,
            "feasibility": self.feasibility,
            "max_turns": self.max_turns,
            "timeout_seconds": self.timeout_seconds,
            "requires_ab_test": self.requires_ab_test,
            "tags": self.tags,
        }
        if self.taxonomy:
            d["taxonomy"] = self.taxonomy.to_dict()
        if self.version != 1:
            d["version"] = self.version
        if self.split:
            d["split"] = self.split
        if self.required_capability:
            d["required_capability"] = self.required_capability
        if self.expected_behavior:
            d["expected_behavior"] = self.expected_behavior
        if self.ambiguity_type:
            d["ambiguity_type"] = self.ambiguity_type
        if self.required_clarifications:
            d["required_clarifications"] = self.required_clarifications
        if self.optional_clarifications:
            d["optional_clarifications"] = self.optional_clarifications
        if self.missing_parameters:
            d["missing_parameters"] = self.missing_parameters
        if self.canary_string:
            d["canary_string"] = self.canary_string
        if self.turns:
            d["turns"] = [t.to_dict() for t in self.turns]
        if self.perturb_fixture:
            d["perturb_fixture"] = self.perturb_fixture
        if self.named_constraints_required:
            d["constraints"]["required_named"] = self.named_constraints_required
        if self.named_constraints_specified:
            d["constraints"]["specified_named"] = self.named_constraints_specified
        if self.tolerance_override:
            d["constraints"]["tolerance_override"] = self.tolerance_override
        if self.gold_intent:
            d["gold_intent"] = self.gold_intent
        if self.reference_solution:
            d["reference_solution"] = self.reference_solution
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Scenario":
        """Create from dictionary (e.g., loaded from YAML). Supports both formats."""
        constraints = data.get("constraints", {})

        # Handle turns format vs flat user_messages
        turns_data = data.get("turns")
        turns = None
        if turns_data:
            # New format: turns is a list of {instruction: ...} dicts
            if turns_data and isinstance(turns_data[0], dict) and "instruction" in turns_data[0]:
                user_messages = [t["instruction"] for t in turns_data]
                turns = None  # New format doesn't use Turn objects (handled by harness)
            else:
                # Legacy format: turns is a list of {user: ...} dicts
                turns = [Turn.from_dict(t) for t in turns_data]
                user_messages = [t.user for t in turns]
        else:
            user_messages = data.get("user_messages", [])

        # Parse taxonomy if present
        taxonomy = None
        taxonomy_data = data.get("taxonomy")
        if taxonomy_data:
            taxonomy = Taxonomy.from_dict(taxonomy_data)

        # Determine level: from taxonomy.scale or legacy level field
        if "level" in data:
            level = Level(data["level"])
        elif taxonomy:
            level = Level(taxonomy.scale.value)  # Scale values match L1/L2/L3
        else:
            level = Level.L1

        # Determine feasibility: from taxonomy or legacy field
        feasibility = data.get("feasibility", "feasible")
        if taxonomy:
            feasibility = taxonomy.feasibility.value

        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            level=level,
            category=data.get("category", ""),
            description=data.get("description", ""),
            fixture=data.get("fixture", "empty"),
            user_messages=user_messages,
            required_constraints=[
                Constraint.from_dict(c) for c in constraints.get("required", [])
                if isinstance(c, dict) and "operator" in c  # Skip named constraints
            ],
            specified_constraints=[
                Constraint.from_dict(c) for c in constraints.get("specified", [])
                if isinstance(c, dict) and "operator" in c
            ],
            validity_constraints=[
                Constraint.from_dict(c) for c in constraints.get("validity", [])
            ],
            expected_changed_entities=data.get("expected_changed_entities", []),
            taxonomy=taxonomy,
            scope=data.get("scope", "sequence"),
            feasibility=feasibility,
            required_capability=data.get("required_capability"),
            expected_behavior=data.get("expected_behavior"),
            ambiguity_type=data.get("ambiguity_type"),
            required_clarifications=data.get("required_clarifications", []),
            optional_clarifications=data.get("optional_clarifications", []),
            missing_parameters=data.get("missing_parameters"),
            turns=turns,
            named_constraints_required=constraints.get("required_named", [
                c for c in constraints.get("required", [])
                if isinstance(c, dict) and "operator" not in c
            ]),
            named_constraints_specified=constraints.get("specified_named", [
                c for c in constraints.get("specified", [])
                if isinstance(c, dict) and "operator" not in c
            ]),
            tolerance_override=constraints.get("tolerance_override"),
            gold_intent=data.get("gold_intent"),
            reference_solution=data.get("reference_solution"),
            canary_string=data.get("canary_string"),
            perturb_fixture=data.get("perturb_fixture", False),
            version=data.get("version", 1),
            split=data.get("split"),
            max_turns=data.get("max_turns", 10),
            timeout_seconds=data.get("timeout_seconds", 60.0),
            requires_ab_test=data.get("requires_ab_test", False),
            tags=data.get("tags", []),
            metadata=data.get("metadata"),
        )


# ============================================================
# Result Models
# ============================================================

@dataclass
class ValidationResult:
    """Result of constraint validation."""
    tsr: bool
    csr: bool
    ovr: float

    # Calibration fields (infeasible/ambiguous)
    refusal_appropriate: Optional[bool] = None
    state_changed: Optional[bool] = None
    asked_clarification: Optional[bool] = None

    # Behavioral classification: "execute" | "refuse" | "clarify" | "noop"
    behavior: Optional[str] = None

    # Feasibility of the scenario being validated — needed for unified SR.
    # "feasible" | "infeasible" | "ambiguous"
    feasibility: Optional[str] = None

    failed_constraints: list[str] = field(default_factory=list)
    error_message: Optional[str] = None

    # Version of the scorer that produced this verdict (see
    # nlebench.runner.constraints.SCORER_VERSION). Additive field: defaults to
    # None so rows scored before versioning deserialize unchanged.
    scorer_version: Optional[str] = None

    @property
    def passed(self) -> bool:
        """
        Unified Success Rate across feasibility types.

        - feasible:   TSR (constraints satisfied) AND CSR (valid output)
        - infeasible: refused appropriately AND state unchanged
        - ambiguous:  asked for clarification AND state unchanged

        If feasibility is unknown, falls back to TSR+CSR (feasible semantics).
        """
        if self.feasibility == "infeasible":
            return bool(self.refusal_appropriate) and self.state_changed is False
        if self.feasibility == "ambiguous":
            return bool(self.asked_clarification) and self.state_changed is False
        # feasible or unknown
        return self.tsr and self.csr


@dataclass
class ExecutionResult:
    """Result of executing a single scenario."""
    scenario_id: str
    run_number: int
    success: bool
    validation: ValidationResult

    # "canonical" | "open" | "canonical_multiturn"
    #
    # `canonical_multiturn` rows come from the closed-loop harness
    # (TrackRunner._run_canonical_multiturn) and are NOT comparable with the
    # published single-shot `canonical` numbers. Never pool the two.
    track: str = "canonical"
    initial_state_json: Optional[str] = None
    final_state_json: Optional[str] = None

    latency_ms: float = 0.0
    token_usage: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    tool_calls: list[dict] = field(default_factory=list)
    agent_response: str = ""
    error_message: Optional[str] = None

    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Turn/token accounting, populated only by the multi-turn track. Left as
    # None for `canonical`/`open` so their serialized rows are byte-identical
    # to before this field existed.
    multiturn_trace: Optional[dict] = None

    # Why this run failed, when the reason is NOT the model's ability.
    #
    #   None                      -> ordinary run; a failure here is a
    #                                capability failure and is read as such.
    #   "context_budget_exceeded" -> the harness stopped the episode because the
    #                                input no longer fit the model's context
    #                                window (see providers/context_budget.py).
    #   "timeout"                 -> wall-clock limit hit.
    #
    # Such runs still count as failures — a benchmark that quietly drops the
    # episodes it could not run would report an inflated score. But they must be
    # separable from capability failures when reporting, which is what this
    # field is for. Serialized only when set, so `canonical`/`open` rows stay
    # byte-identical to before the field existed.
    failure_kind: Optional[str] = None

    def __post_init__(self):
        """Make a mislabelled multi-turn row impossible to construct.

        The multi-turn track is a different measurement from the published
        single-shot Canonical track, and the two must never be pooled. A plain
        string assignment is not enough protection: a wiring mistake anywhere
        upstream would produce rows that *look* canonical and would be silently
        aggregated with the published numbers.

        A ``multiturn_trace`` can only be produced by
        ``TrackRunner._run_canonical_multiturn``. Its presence is therefore
        proof of which loop ran, and pairing it with any other track value is
        rejected at construction time rather than at analysis time — by which
        point the run has already been written to disk.
        """
        valid = {t.value for t in BenchmarkTrack}
        if self.track not in valid:
            raise ValueError(
                f"ExecutionResult.track must be one of {sorted(valid)}, "
                f"got {self.track!r}"
            )
        if (
            self.multiturn_trace is not None
            and self.track not in CLOSED_LOOP_TRACK_VALUES
        ):
            raise ValueError(
                "ExecutionResult carries a multiturn_trace but is tagged "
                f"track={self.track!r}. A closed-loop result can only be "
                f"recorded as one of {sorted(CLOSED_LOOP_TRACK_VALUES)} — "
                "multi-turn and single-shot numbers are not poolable."
            )

    def to_dict(self) -> dict:
        d = {
            "scenario_id": self.scenario_id,
            "run_number": self.run_number,
            "success": self.success,
            "track": self.track,
            "validation": {
                "tsr": self.validation.tsr,
                "csr": self.validation.csr,
                "ovr": self.validation.ovr,
                "refusal_appropriate": self.validation.refusal_appropriate,
                "state_changed": self.validation.state_changed,
                "asked_clarification": self.validation.asked_clarification,
                "behavior": self.validation.behavior,
                "feasibility": self.validation.feasibility,
                "failed_constraints": self.validation.failed_constraints,
                "error_message": self.validation.error_message,
            },
            "latency_ms": self.latency_ms,
            "token_usage": self.token_usage,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "tool_calls": self.tool_calls,
            "agent_response": self.agent_response,
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
        if self.initial_state_json is not None:
            d["initial_state_json"] = self.initial_state_json
        if self.final_state_json is not None:
            d["final_state_json"] = self.final_state_json
        if self.multiturn_trace is not None:
            d["multiturn_trace"] = self.multiturn_trace
        if self.failure_kind is not None:
            d["failure_kind"] = self.failure_kind
        return d


@dataclass
class MetricResults:
    """Aggregated metrics from multiple execution results."""
    tsr: float = 0.0
    rar: float = 0.0
    cqs: float = 0.0
    csr: float = 0.0
    ovr: float = 0.0

    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    cpr: float = 0.0  # Cost Per Request (USD)

    nos: float = 0.0  # NLEBench Overall Score (0-100)

    total_runs: int = 0
    successful_runs: int = 0

    # Breakdowns
    tsr_by_level: dict[str, float] = field(default_factory=dict)
    tsr_by_feasibility: dict[str, float] = field(default_factory=dict)
    tsr_by_cogtype: dict[str, float] = field(default_factory=dict)  # NEW

    def to_dict(self) -> dict:
        return {
            "tsr": self.tsr,
            "rar": self.rar,
            "cqs": self.cqs,
            "csr": self.csr,
            "ovr": self.ovr,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "cpr": self.cpr,
            "nos": self.nos,
            "total_runs": self.total_runs,
            "successful_runs": self.successful_runs,
            "tsr_by_level": self.tsr_by_level,
            "tsr_by_feasibility": self.tsr_by_feasibility,
            "tsr_by_cogtype": self.tsr_by_cogtype,
        }
